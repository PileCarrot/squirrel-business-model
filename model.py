"""Monthly and annual POTI financial scenarios, using only the standard library."""
import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def ratio(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator else None


def sum_known(values):
    values = list(values)
    return None if None in values else sum(values)


def ai_event_cost(c, event):
    direct = c.get(f"ai_{event}_gbp")
    if direct is not None:
        return direct
    inputs = [c.get(f"{event}_{kind}_tokens") for kind in ("input", "output")]
    rates = [c.get(f"ai_{kind}_gbp_per_million") for kind in ("input", "output")]
    if None in [*inputs, *rates]:
        return None
    return sum(tokens * rate for tokens, rate in zip(inputs, rates)) / 1_000_000


def bank_unit_cost(c, p, tier):
    per_user = c.get("bank_gbp_per_paid_user_month")
    if per_user is not None:
        return per_user
    rate, links = c.get("bank_link_gbp"), c.get(f"{tier}_average_links")
    if links is not None and not 0 <= links <= p[f"{tier}_account_limit"]:
        raise ValueError(f"{tier} average links must be within the tier limit")
    return None if None in (rate, links) else rate * links


def direct_cost(c, p, bank_cost):
    chat, report = ai_event_cost(c, "chat"), ai_event_cost(c, "report")
    return sum_known([bank_cost, c.get("other_paid_user_month_gbp"),
                      None if chat is None else chat * p["monthly_ai_chats"],
                      None if report is None else report * p["weekly_ai_reports"] * 52 / 12])


def tier_total(counts, costs):
    return sum_known(0 if n == 0 else None if cost is None else n * cost
                     for n, cost in zip(counts, costs))


def partnership_drivers(data, month, mau, campus_mau=0, free_mau=None):
    bank = data.get("bank_referrals", {})
    ads = data.get("merchant_ads", {})
    bank_start, ad_start = bank.get("start_month"), ads.get("start_month")
    openings, commission = bank.get("monthly_eligible_openings"), bank.get("gbp_per_eligible_opening")
    noncampus_rate = bank.get("monthly_eligible_openings_per_mau")
    campus_rate = bank.get("monthly_eligible_campus_openings_per_mau", noncampus_rate)
    if openings is None and None not in (noncampus_rate, campus_rate):
        openings = (mau - campus_mau) * noncampus_rate + campus_mau * campus_rate
    bank_revenue = 0 if bank_start is None or month < bank_start else None
    if bank_revenue is None and openings is not None and commission is not None:
        bank_revenue = openings * commission

    stickiness = ads.get("dau_to_mau")
    fill_rate = ads.get("fill_rate")
    for rate in (stickiness, fill_rate):
        if rate is not None and not 0 <= rate <= 1:
            raise ValueError("DAU/MAU and ad fill rate must lie between zero and one")
    dau = None if stickiness is None else mau * stickiness
    impressions = ads.get("impressions_per_daily_active_user")
    cpm = ads.get("net_cpm_gbp")
    ad_revenue = 0 if ad_start is None or month < ad_start else None
    free_user_impressions = ads.get("impressions_per_monthly_free_user")
    if ad_revenue is None:
        if free_user_impressions is not None:
            if None not in (free_mau, fill_rate, cpm):
                ad_revenue = free_mau * free_user_impressions * fill_rate * cpm / 1000
        elif None not in (dau, impressions, fill_rate, cpm):
            ad_revenue = dau * (365.25 / 12) * impressions * fill_rate * cpm / 1000
    return dau, bank_revenue, ad_revenue


def commerce_lines(scenario, month, mau, plus_paid):
    v = scenario.get("vouchers")
    h = scenario.get("savings")
    orders = voucher_revenue = savers = balances = savings_revenue = operating_cost = 0
    if v and v.get("start_month") is not None and month >= v["start_month"]:
        orders = mau * v["approved_orders_per_mau"]
        voucher_revenue = orders * v["average_order_gbp"] * v["net_commission_share"]
        operating_cost = v["monthly_ops_gbp"] + orders * v["cost_per_order_gbp"]
    if h and h.get("start_month") is not None and month >= h["start_month"]:
        savers = plus_paid * h["funded_plus_share"]
        balances = savers * h["average_funded_balance_gbp"]
        savings_revenue = balances * h["annual_net_balance_share"] / 12
        operating_cost += h["monthly_ops_gbp"]
        if month == h["start_month"]:
            operating_cost += h["setup_gbp"]
    return orders, voucher_revenue, savers, balances, savings_revenue, operating_cost


def simulate(data, name):
    p, g, c = (data[key] for key in ("product", "growth", "costs"))
    s = data["scenarios"][name]
    g = {**g, **s.get("growth", {})}
    c = {**c, **s.get("costs", {})}
    data = {**data, **{key: {**data.get(key, {}), **s.get(key, {})}
                       for key in ("bank_referrals", "merchant_ads", "vouchers", "savings")}}
    campus = s.get("campus")
    contracts = data.get("partnerships", [])
    aliases = {"bank_referral": "bank_referrals", "voucher": "vouchers",
               "university_license": "campus", "university_licence": "campus",
               "university": "campus", "savings_balance_share": "savings",
               "app_open_ads": "merchant_ads", "coupon_commission": "vouchers"}
    driver_starts = {key: data[key].get("start_month")
                     for key in ("bank_referrals", "merchant_ads", "vouchers", "savings")}
    driver_starts["campus"] = min(campus["launch_months"]) if campus else None
    for contract in contracts:
        stream = contract["stream"].strip().lower().replace("-", "_").replace(" ", "_")
        start = driver_starts.get(aliases.get(stream, stream))
        if start is not None and contract["end_month"] >= start:
            raise ValueError(f"Manual contract overlaps the modelled revenue stream: {stream}")
    mix = g["plus_paid_mix"]
    shares = list(g["year_end_paid_share"])
    fixed_keys = ("annual_marketing_gbp", "annual_compliance_gbp", "annual_base_hosting_gbp")
    years = len(g["year_end_mau"])
    for series in [g["year_end_registered"], shares, *(c[k] for k in fixed_keys)]:
        if len(series) != years:
            raise ValueError("Annual series must match the MAU horizon")
    other = c["annual_other_cash_gbp"]
    if other is not None and len(other) != years:
        raise ValueError("Other annual cash costs must match the MAU horizon")
    override = s.get("paid_share_override")
    if override is not None:
        shares = [override] * years
    churn = g["monthly_paid_churn"]
    fee = c["subscription_fee_share"]
    vat = c.get("vat_rate", 0.2)
    for rate in [mix, *shares, churn, fee, vat]:
        if rate is not None and not 0 <= rate <= 1:
            raise ValueError("Rates must lie between zero and one")
    prices = (p["starter_gbp"], s.get("plus_price_override", p["plus_gbp"]))
    net_prices = tuple(price / (1 + vat) for price in prices) if vat is not None else (None, None)
    proxy = s.get("paid_user_month_cost_proxy_gbp")
    bank_costs = tuple(bank_unit_cost(c, p, tier) for tier in ("starter", "plus")) if proxy is None else (None, None)
    tier_costs = (proxy, proxy) if proxy is not None else tuple(
        direct_cost(c, p, bank_cost) for bank_cost in bank_costs
    )
    # A scenario proxy covers modeled direct service costs. Selling fees are independent.
    margins = [None if None in (cost, price) else price * (1 - (fee or 0)) - cost
               for price, cost in zip(net_prices, tier_costs)]
    balance = c["cash_opening_gbp"]
    previous_mau = previous_registered = previous_paid = 0
    cumulative_partial = 0
    rows = []
    active_stock = 0
    campus_active = campus_registered = live_schools = 0
    campus_cohorts = []
    campus_eligibility = 0
    campus_contract_value = 0
    weights = g.get("registration_weights")
    if weights is not None:
        if len(weights) != 12 or min(weights) < 0 or sum(weights) <= 0:
            raise ValueError("Registration weights must define 12 nonnegative months")
        if any(not 0 <= g[k] <= 1 for k in ("activation_rate", "monthly_active_churn")):
            raise ValueError("Activation and churn must lie between zero and one")
        weights = [weight / sum(weights) for weight in weights]
    for year, (mau, registered, share) in enumerate(
        zip(g["year_end_mau"], g["year_end_registered"], shares), start=1
    ):
        if not 0 <= mau <= registered or registered < previous_registered:
            raise ValueError("MAU must fit cumulative registrations, which cannot decrease")
        for period in range(1, 13):
            weight = period / 12
            month = (year - 1) * 12 + period
            active = previous_mau + weight * (mau - previous_mau)
            total_registered = previous_registered + weight * (registered - previous_registered)
            if weights is not None:
                new_registrations = (registered - previous_registered) * weights[period - 1]
                total_registered = previous_registered + (registered - previous_registered) * sum(weights[:period])
                active_stock = active_stock * (1 - g["monthly_active_churn"]) + new_registrations * g["activation_rate"]
                active = active_stock
            prior_share = shares[year - 2] if year > 1 else share
            paid_share = prior_share + weight * (share - prior_share)
            paid = active * paid_share
            counts = (paid * (1 - mix), paid * mix)
            license_revenue = campus_cost = campus_upgrades = 0
            campus_active *= 1 - g.get("monthly_active_churn", 0)
            if campus:
                if month == campus["launch_months"][year - 1]:
                    next_schools = campus["live_schools"][year - 1]
                    campus_cost = max(0, next_schools - live_schools) * campus["setup_per_new_school_gbp"]
                    live_schools = next_schools
                    if "eligible_students" in campus:
                        eligible = campus["eligible_students"][year - 1]
                        campus_cohorts.append((month, max(0, eligible - campus_eligibility)))
                        campus_eligibility = eligible
                        campus_contract_value = campus["annual_contract_value_gbp"][year - 1]
                    else:
                        campus_new = live_schools * campus["billed_seats_per_school"] * campus["incremental_registration_share"]
                        campus_registered += campus_new
                        campus_active += campus_new * g["activation_rate"]
                if "eligible_students" in campus:
                    launch_share = campus["average_mau_share"]
                    subsequent_share = (campus["first_year_registration_share"] - launch_share) / 11
                    campus_registered += sum(
                        students * (launch_share if month == launch else subsequent_share)
                        for launch, students in campus_cohorts if month - launch < 12
                    )
                    campus_active = campus_eligibility * launch_share
                    license_revenue = campus_contract_value / 12
                else:
                    license_revenue = live_schools * campus["billed_seats_per_school"] * campus["annual_gbp_per_seat"] / 12
                campus_cost += live_schools * campus["annual_ops_per_school_gbp"] / 12
                campus_upgrades = campus_active * paid_share * mix
            active += campus_active
            total_registered += campus_registered
            subscription_gross = sum(n * price for n, price in zip(counts, prices))
            subscription_gross += campus_upgrades * (prices[1] - prices[0])
            subscription = subscription_gross / (1 + vat) if vat is not None else None
            booked = {}
            for contract in contracts:
                if contract["start_month"] <= month <= contract["end_month"]:
                    stream = contract["stream"]
                    booked[stream] = booked.get(stream, 0) + contract["monthly_gbp"]
            free_noncampus_mau = max(0, active - campus_active - paid)
            dau, bank_referral, ads = partnership_drivers(
                data, month, active, campus_active, free_noncampus_mau
            )
            orders, voucher, savers, balances, savings, commerce_cost = commerce_lines(data, month, active, counts[1] + campus_upgrades)
            partnership = sum_known([sum(booked.values()), bank_referral, ads, voucher, savings, license_revenue])
            revenue = sum_known([subscription, partnership])
            service_counts = (counts[0] + campus_active - campus_upgrades, counts[1] + campus_upgrades)
            service = tier_total(service_counts, tier_costs)
            fixed = sum(c[k][year - 1] for k in fixed_keys) / 12
            selling_fee = None if fee is None or subscription is None else subscription * fee
            bank_spend = tier_total(service_counts, bank_costs) if proxy is None else None
            bank_minimum = c.get("bank_monthly_minimum_gbp", 0)
            minimum_top_up = 0
            if proxy is None and bank_minimum != 0:
                minimum_top_up = None if None in (bank_minimum, bank_spend) else max(0, bank_minimum - bank_spend)
            partial_cost = sum_known([fixed, service, minimum_top_up, commerce_cost,
                                      campus_cost, selling_fee if selling_fee is not None else 0])
            other_cash = None if other is None else other[year - 1] / 12
            total_cost = sum_known([partial_cost, other_cash]) if selling_fee is not None else None
            partial_net = revenue - partial_cost if None not in (revenue, partial_cost) else None
            cumulative_partial = sum_known([cumulative_partial, partial_net])
            net = revenue - total_cost if None not in (revenue, total_cost) else None
            balance = sum_known([balance, net])
            new_paid = paid - previous_paid * (1 - churn) if churn is not None else None
            cac = ratio(c[fixed_keys[0]][year - 1] / 12, new_paid) if new_paid and new_paid > 0 else None
            blended_margin = sum_known([margins[0] * (1 - mix) if margins[0] is not None else None,
                                        margins[1] * mix if margins[1] is not None else None])
            rows.append(dict(scenario=name, month=month, year=year, registered=total_registered,
                             mau=active, estimated_dau=dau, paid=paid + campus_upgrades, starter_paid=counts[0], plus_paid=counts[1] + campus_upgrades,
                             campus_schools=live_schools, campus_mau=campus_active,
                             campus_registered=campus_registered, campus_plus_upgrades=campus_upgrades,
                             university_license_gbp=license_revenue, campus_cost_gbp=campus_cost,
                             subscription_gross_gbp=subscription_gross, subscription_gbp=subscription, partnership_gbp=partnership,
                             bank_referral_gbp=bank_referral, merchant_ads_gbp=ads,
                             voucher_orders=orders, voucher_gbp=voucher, funded_savers=savers,
                             savings_balance_gbp=balances, savings_gbp=savings,
                             commerce_operating_cost_gbp=commerce_cost,
                             revenue_gbp=revenue, fixed_modeled_gbp=fixed, service_gbp=service,
                             selling_fee_gbp=selling_fee, other_cash_gbp=other_cash,
                             bank_usage_gbp=bank_spend, bank_minimum_top_up_gbp=minimum_top_up,
                             noncampus_mau_anchor=mau if period == 12 else None,
                             noncampus_mau_vs_anchor=active - campus_active - mau if period == 12 else None,
                             partial_cost_gbp=partial_cost,
                             partial_net_gbp=partial_net, cumulative_partial_net_gbp=cumulative_partial,
                             total_cost_gbp=total_cost, net_gbp=net, cash_balance_gbp=balance,
                             starter_modeled_margin_gbp=margins[0], plus_modeled_margin_gbp=margins[1],
                             implied_paid_cac_at_target_gbp=cac,
                             modeled_contribution_ltv_gbp=ratio(blended_margin, churn)
                             if blended_margin is not None else None))
            previous_paid = paid
        previous_mau, previous_registered = mau, registered
    return rows


def corporation_tax(profit, finance):
    if profit <= finance["corporation_tax_small_threshold_gbp"]:
        return profit * finance["corporation_tax_small_rate"]
    relief = max(0, finance["corporation_tax_upper_threshold_gbp"] - profit)
    return (profit * finance["corporation_tax_main_rate"]
            - relief * finance["corporation_tax_marginal_fraction"])


def annualize(rows, finance=None):
    sums = ("subscription_gross_gbp", "selling_fee_gbp", "other_cash_gbp", "bank_usage_gbp", "university_license_gbp", "campus_cost_gbp", "voucher_gbp", "savings_gbp", "commerce_operating_cost_gbp", "subscription_gbp", "partnership_gbp", "bank_referral_gbp", "merchant_ads_gbp",
            "revenue_gbp", "fixed_modeled_gbp",
            "service_gbp", "bank_minimum_top_up_gbp", "partial_cost_gbp", "partial_net_gbp", "total_cost_gbp", "net_gbp")
    losses = [0, 0]
    cumulative_after_tax = [0, 0]
    for start in range(0, len(rows), 12):
        months = rows[start:start + 12]
        end = months[-1]
        annual = {key: end[key] for key in ("scenario", "year", "registered", "mau", "paid",
                                           "cumulative_partial_net_gbp", "cash_balance_gbp",
                                           "noncampus_mau_anchor", "noncampus_mau_vs_anchor")}
        annual.update({key: sum_known(row[key] for row in months) for key in sums})
        cost = annual["partial_cost_gbp"]
        annual["partial_cost_to_revenue"] = ratio(cost, annual["revenue_gbp"]) if cost is not None else None
        if finance is not None:
            year = end["year"] - 1
            headcount = finance["salary_headcount"][year]
            salary = finance["annual_salary_per_person_gbp"][year]
            gross_pay = headcount * salary
            gross_ni = headcount * max(0, salary - finance["employer_ni_threshold_gbp"]) * finance["employer_ni_rate"]
            allowance = min(gross_ni, finance["employment_allowance_gbp"])
            pensionable = max(0, min(salary, finance["pension_upper_gbp"]) - finance["pension_lower_gbp"])
            pension = headcount * pensionable * finance["employer_pension_rate"]
            payroll = gross_pay + gross_ni - allowance + pension
            setup = finance["incorporation_and_setup_gbp"][year]
            filing = finance["confirmation_statement_gbp"][year]
            pretax = sum_known([annual["partial_net_gbp"], -payroll, -setup, -filing])
            if annual["other_cash_gbp"] is not None:
                pretax = sum_known([pretax, -annual["other_cash_gbp"]])
            grant = finance["competitive_grant_upside_gbp"][year]
            annual.update(
                salary_headcount=headcount,
                annual_salary_per_person_gbp=salary,
                weekly_paid_hours=finance["weekly_paid_hours"][year],
                payroll_gross_gbp=gross_pay,
                employer_ni_before_allowance_gbp=gross_ni,
                employment_allowance_gbp=allowance,
                employer_pension_gbp=pension,
                payroll_cost_gbp=payroll,
                incorporation_and_setup_gbp=setup,
                confirmation_statement_gbp=filing,
                pretax_after_payroll_and_setup_gbp=pretax,
                competitive_grant_upside_gbp=grant,
            )
            for i, (grant_amount, suffix) in enumerate(((0, ""), (grant, "_with_grant"))):
                taxable_before_losses = sum_known([pretax, setup, grant_amount])
                taxable = sum_known([taxable_before_losses, None if losses[i] is None else -losses[i]])
                tax = None if taxable is None else corporation_tax(max(0, taxable), finance)
                losses[i] = None if taxable is None else max(0, -taxable)
                after_tax = sum_known([pretax, grant_amount, None if tax is None else -tax])
                cumulative_after_tax[i] = sum_known([cumulative_after_tax[i], after_tax])
                annual[f"tax_losses_carried_forward{suffix}_gbp"] = losses[i]
                annual[f"corporation_tax{suffix}_gbp"] = tax
                annual[f"after_tax_result{suffix}_gbp"] = after_tax
                annual[f"cumulative_after_tax_result{suffix}_gbp"] = cumulative_after_tax[i]
        yield annual


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as file:
        fields = dict.fromkeys(key for row in rows for key in row)
        writer = csv.DictWriter(file, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows({k: round(v, 6) if isinstance(v, float) else v
                          for k, v in row.items()} for row in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, default=ROOT / "parameters.json")
    parser.add_argument("--out", type=Path, default=ROOT / "results")
    args = parser.parse_args()
    data = json.loads(args.parameters.read_text(encoding="utf-8"))
    monthly, annual = [], []
    for name in data["scenarios"]:
        rows = simulate(data, name)
        monthly.extend(rows)
        annual.extend(annualize(rows, data["scenarios"][name].get("annual_finance")))
    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "monthly.csv", monthly)
    write_csv(args.out / "annual.csv", annual)
    print(f"{len(monthly)} monthly and {len(annual)} annual rows in {args.out}")


if __name__ == "__main__":
    main()
