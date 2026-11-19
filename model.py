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


def direct_cost(c, p, tier):
    inputs = [c["ai_chat_gbp"], c["ai_report_gbp"], c["bank_link_gbp"],
              c[f"{tier}_average_links"], c["other_paid_user_month_gbp"]]
    if None in inputs:
        return None
    chat, report, link, links, other = inputs
    if not 0 <= links <= p[f"{tier}_account_limit"]:
        raise ValueError(f"{tier} average links must be within the tier limit")
    return chat * p["monthly_ai_chats"] + report * p["weekly_ai_reports"] * 52 / 12 + link * links + other


def simulate(data, name):
    p, g, c = (data[key] for key in ("product", "growth", "costs"))
    s = data["scenarios"][name]
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
            partnership = sum(booked.values())
            revenue = subscription + partnership
            service = sum_known(n * cost if cost is not None else None
                                for n, cost in zip(counts, tier_costs))
            fixed = sum(c[k][year - 1] for k in fixed_keys) / 12
            selling_fee = None if fee is None else subscription * fee
            partial_cost = sum_known([fixed, service])
            total_cost = sum_known([partial_cost, selling_fee,
                                    None if other is None else other[year - 1] / 12])
            partial_net = revenue - partial_cost if partial_cost is not None else None
            cumulative_partial = sum_known([cumulative_partial, partial_net])
            net = revenue - total_cost if total_cost is not None else None
            balance = sum_known([balance, net])
            new_paid = paid - previous_paid * (1 - churn) if churn is not None else None
            cac = ratio(c[fixed_keys[0]][year - 1] / 12, new_paid) if new_paid and new_paid > 0 else None
            blended_margin = sum_known([margins[0] * (1 - mix) if margins[0] is not None else None,
                                        margins[1] * mix if margins[1] is not None else None])
            rows.append(dict(scenario=name, month=month, year=year, registered=total_registered,
                             mau=active, paid=paid, starter_paid=counts[0], plus_paid=counts[1],
                             subscription_gbp=subscription, partnership_gbp=partnership,
                             revenue_gbp=revenue, fixed_modeled_gbp=fixed, service_gbp=service,
                             selling_fee_gbp=selling_fee, partial_cost_gbp=partial_cost,
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
    sums = ("subscription_gbp", "partnership_gbp", "revenue_gbp", "fixed_modeled_gbp",
            "service_gbp", "partial_cost_gbp", "partial_net_gbp", "total_cost_gbp", "net_gbp")
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
