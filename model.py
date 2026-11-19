"""Monthly and annual POTI financial scenarios, using only the standard library."""
import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def sum_known(values):
    values = list(values)
    return None if None in values else sum(values)


def ai_event_cost(c, event):
    direct = c[f"ai_{event}_gbp"]
    if direct is not None:
        return direct
    inputs = [c.get(f"{event}_{kind}_tokens") for kind in ("input", "output")]
    rates = [c.get(f"ai_{kind}_gbp_per_million") for kind in ("input", "output")]
    if None in [*inputs, *rates]:
        return None
    return sum(tokens * rate for tokens, rate in zip(inputs, rates)) / 1_000_000


def direct_cost(c, p, tier):
    per_user = c.get("bank_gbp_per_paid_user_month")
    if per_user is not None:
        chat, report, other = ai_event_cost(c, "chat"), ai_event_cost(c, "report"), c["other_paid_user_month_gbp"]
        return sum_known([per_user, other, None if chat is None else chat * p["monthly_ai_chats"],
                          None if report is None else report * p["weekly_ai_reports"] * 52 / 12])
    inputs = [ai_event_cost(c, "chat"), ai_event_cost(c, "report"), c["bank_link_gbp"],
              c[f"{tier}_average_links"], c["other_paid_user_month_gbp"]]
    if None in inputs:
        return None
    chat, report, link, links, other = inputs
    if not 0 <= links <= p[f"{tier}_account_limit"]:
        raise ValueError(f"{tier} average links must be within the tier limit")
    return chat * p["monthly_ai_chats"] + report * p["weekly_ai_reports"] * 52 / 12 + link * links + other


def partnership_drivers(data, month, mau):
    bank = data.get("bank_referrals", {})
    ads = data.get("merchant_ads", {})
    bank_start, ad_start = bank.get("start_month"), ads.get("start_month")
    openings, commission = bank.get("monthly_eligible_openings"), bank.get("gbp_per_eligible_opening")
    if openings is None and bank.get("monthly_eligible_openings_per_mau") is not None:
        openings = mau * bank["monthly_eligible_openings_per_mau"]
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
    if ad_revenue is None and None not in (dau, impressions, fill_rate, cpm):
        ad_revenue = dau * (365.25 / 12) * impressions * fill_rate * cpm / 1000
    return dau, bank_revenue, ad_revenue


def simulate(data, name):
    p, g, c = (data[key] for key in ("product", "growth", "costs"))
    s = data["scenarios"][name]
    g = {**g, **s.get("growth", {})}
    c = {**c, **s.get("costs", {})}
    data = {**data, **{key: {**data.get(key, {}), **s.get(key, {})}
                       for key in ("bank_referrals", "merchant_ads")}}
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
    for rate in [mix, *shares, churn, fee]:
        if rate is not None and not 0 <= rate <= 1:
            raise ValueError("Rates must lie between zero and one")
    prices = (p["starter_gbp"], s.get("plus_price_override", p["plus_gbp"]))
    proxy = s.get("paid_user_month_cost_proxy_gbp")
    tier_costs = (proxy, proxy) if proxy is not None else tuple(
        direct_cost(c, p, tier) for tier in ("starter", "plus")
    )
    # A scenario proxy covers modeled direct service costs. Selling fees are independent.
    margins = [None if cost is None else price * (1 - (fee or 0)) - cost
               for price, cost in zip(prices, tier_costs)]
    balance = c["cash_opening_gbp"]
    previous_mau = previous_registered = previous_paid = 0
    cumulative_partial = 0
    rows = []
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
            prior_share = shares[year - 2] if year > 1 else share
            paid = active * (prior_share + weight * (share - prior_share))
            counts = (paid * (1 - mix), paid * mix)
            subscription = sum(n * price for n, price in zip(counts, prices))
            booked = {}
            for contract in data["partnerships"]:
                if contract["start_month"] <= month <= contract["end_month"]:
                    stream = contract["stream"]
                    booked[stream] = booked.get(stream, 0) + contract["monthly_gbp"]
            dau, bank_referral, ads = partnership_drivers(data, month, active)
            partnership = sum_known([sum(booked.values()), bank_referral, ads])
            revenue = sum_known([subscription, partnership])
            service = sum_known(n * cost if cost is not None else None
                                for n, cost in zip(counts, tier_costs))
            fixed = sum(c[k][year - 1] for k in fixed_keys) / 12
            selling_fee = None if fee is None else subscription * fee
            minimum_top_up = max(0, c.get("bank_monthly_minimum_gbp", 0)
                                 - paid * c.get("bank_gbp_per_paid_user_month", 0)) if proxy is None else 0
            partial_cost = sum_known([fixed, service, minimum_top_up])
            total_cost = sum_known([partial_cost, selling_fee,
                                    None if other is None else other[year - 1] / 12])
            partial_net = revenue - partial_cost if None not in (revenue, partial_cost) else None
            cumulative_partial = sum_known([cumulative_partial, partial_net])
            net = revenue - total_cost if None not in (revenue, total_cost) else None
            balance = sum_known([balance, net])
            new_paid = paid - previous_paid * (1 - churn) if churn is not None else None
            cac = ratio(c[fixed_keys[0]][year - 1] / 12, new_paid) if new_paid and new_paid > 0 else None
            blended_margin = sum_known([margins[0] * (1 - mix) if margins[0] is not None else None,
                                        margins[1] * mix if margins[1] is not None else None])
            rows.append(dict(scenario=name, month=month, year=year, registered=total_registered,
                             mau=active, estimated_dau=dau, paid=paid, starter_paid=counts[0], plus_paid=counts[1],
                             subscription_gbp=subscription, partnership_gbp=partnership,
                             bank_referral_gbp=bank_referral, merchant_ads_gbp=ads,
                             revenue_gbp=revenue, fixed_modeled_gbp=fixed, service_gbp=service,
                             selling_fee_gbp=selling_fee, bank_minimum_top_up_gbp=minimum_top_up,
                             partial_cost_gbp=partial_cost,
                             partial_net_gbp=partial_net, cumulative_partial_net_gbp=cumulative_partial,
                             total_cost_gbp=total_cost, net_gbp=net, cash_balance_gbp=balance,
                             starter_modeled_margin_gbp=margins[0], plus_modeled_margin_gbp=margins[1],
                             modeled_paid_cac_gbp=cac,
                             modeled_contribution_ltv_gbp=ratio(blended_margin, churn)
                             if blended_margin is not None else None))
            previous_paid = paid
        previous_mau, previous_registered = mau, registered
    return rows


def annualize(rows):
    sums = ("subscription_gbp", "partnership_gbp", "bank_referral_gbp", "merchant_ads_gbp",
            "revenue_gbp", "fixed_modeled_gbp",
            "service_gbp", "bank_minimum_top_up_gbp", "partial_cost_gbp", "partial_net_gbp", "total_cost_gbp", "net_gbp")
    for start in range(0, len(rows), 12):
        months = rows[start:start + 12]
        end = months[-1]
        annual = {key: end[key] for key in ("scenario", "year", "registered", "mau", "paid",
                                           "cumulative_partial_net_gbp", "cash_balance_gbp")}
        annual.update({key: sum_known(row[key] for row in months) for key in sums})
        cost = annual["partial_cost_gbp"]
        annual["partial_cost_to_revenue"] = ratio(cost, annual["revenue_gbp"]) if cost is not None else None
        yield annual


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
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
        annual.extend(annualize(rows))
    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "monthly.csv", monthly)
    write_csv(args.out / "annual.csv", annual)
    print(f"{len(monthly)} monthly and {len(annual)} annual rows in {args.out}")


if __name__ == "__main__":
    main()
