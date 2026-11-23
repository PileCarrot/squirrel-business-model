import copy
import json
import unittest

from model import ROOT, ai_event_cost, annualize, simulate


class FinancialModelTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "parameters.json").read_text())
        self.costs = self.data["costs"]

    def test_bank_minimum_offsets_connection_usage(self):
        self.costs.update(bank_gbp_per_paid_user_month=None,
                          bank_link_gbp=.5, starter_average_links=1,
                          plus_average_links=2, bank_monthly_minimum_gbp=100)
        rows = simulate(self.data, "quoted_costs")
        self.assertAlmostEqual(rows[0]["bank_minimum_top_up_gbp"], 91.25)
        self.assertAlmostEqual(rows[11]["bank_usage_gbp"], 105)
        self.assertEqual(rows[11]["bank_minimum_top_up_gbp"], 0)
        self.costs["bank_monthly_minimum_gbp"] = None
        self.assertIsNone(simulate(self.data, "quoted_costs")[11]["bank_minimum_top_up_gbp"])
        self.costs.update(bank_link_gbp=None, bank_monthly_minimum_gbp=100)
        self.assertIsNone(simulate(self.data, "quoted_costs")[11]["bank_minimum_top_up_gbp"])

    def test_vat_and_fees_reconcile(self):
        self.costs["annual_other_cash_gbp"] = [1200, 1200, 1200]
        row = next(annualize(simulate(self.data, "uk_campus_licensing")))
        self.assertAlmostEqual(row["subscription_gbp"], row["subscription_gross_gbp"] / 1.2)
        self.assertAlmostEqual(row["selling_fee_gbp"], row["subscription_gbp"] * .15)
        components = ("fixed_modeled_gbp", "service_gbp", "bank_minimum_top_up_gbp",
                      "commerce_operating_cost_gbp", "campus_cost_gbp", "selling_fee_gbp")
        self.assertAlmostEqual(row["partial_cost_gbp"], sum(row[key] for key in components))
        self.assertAlmostEqual(row["total_cost_gbp"], row["partial_cost_gbp"] + 1200)

    def test_duplicate_stream_and_optional_contracts(self):
        self.data.pop("partnerships", None)
        self.assertEqual(len(simulate(self.data, "uk_campus_licensing")), 36)
        self.data["partnerships"] = [dict(stream="bank-referral", start_month=13,
                                          end_month=14, monthly_gbp=100)]
        with self.assertRaisesRegex(ValueError, "overlaps"):
            simulate(self.data, "uk_campus_licensing")

    def test_token_fallback_and_baseline_voucher_config(self):
        self.costs.pop("ai_chat_gbp")
        self.costs.update(chat_input_tokens=1000, chat_output_tokens=500,
                          ai_input_gbp_per_million=1, ai_output_gbp_per_million=2)
        self.assertAlmostEqual(ai_event_cost(self.costs, "chat"), .002)
        self.data["vouchers"] = copy.deepcopy(self.data["scenarios"]["uk_campus_licensing"]["vouchers"])
        self.assertGreater(simulate(self.data, "base_draft")[9]["voucher_gbp"], 0)

    def test_investor_growth_reconciles_to_the_review_sheet(self):
        name = "uk_eu_investor_growth"
        inputs = self.data["scenarios"][name]
        monthly = simulate(self.data, name)
        annual = list(annualize(monthly, inputs["annual_finance"]))
        expected = (
            (25000, 3, 6000, 0, 22192.66, 22374.38, -41607.32, -41607.32),
            (90000, 8, 81000, 14325.86, 166215.15, 59958.76, 29517.99, 51950.97),
            (300000, 14, 288000, 51412.66, 598458.14, 203838.71, 203286.01, 200413.68),
        )
        fields = ("registered", "university_license_gbp", "bank_referral_gbp",
                  "revenue_gbp", "partial_cost_gbp", "after_tax_result_gbp",
                  "after_tax_result_with_grant_gbp")
        for i, (row, (registered, schools, *amounts)) in enumerate(zip(annual, expected)):
            end = monthly[(i + 1) * 12 - 1]
            self.assertEqual(end["campus_schools"], schools)
            self.assertAlmostEqual(end["registered"], sum(
                series[i] for series in inputs["registration_targets_by_market"].values()
            ))
            self.assertEqual(tuple(round(row[field], 2) for field in fields), (registered, *amounts))

        for row in monthly:
            month = row["month"]
            noncampus_mau = row["mau"] - row["campus_mau"]
            noncampus_paid = row["paid"] - row["campus_plus_upgrades"]
            campus_upgrade = row["campus_plus_upgrades"]
            self.assertAlmostEqual(row["merchant_ads_gbp"], (noncampus_mau - noncampus_paid)
                                   * 4 * .6 * 3 / 1000 if month >= 10 else 0)
            self.assertAlmostEqual(row["bank_referral_gbp"], (noncampus_mau * .003
                                   + row["campus_mau"] * .004) * 20 if month >= 13 else 0)
            self.assertAlmostEqual(row["subscription_gross_gbp"], row["starter_paid"] * 3.99
                                   + (row["plus_paid"] - campus_upgrade) * 7.99 + campus_upgrade * 4)
            self.assertAlmostEqual(row["bank_usage_gbp"] + row["bank_minimum_top_up_gbp"],
                                   max(250, (noncampus_paid + row["campus_mau"]) * .25))
            self.assertAlmostEqual(row["revenue_gbp"], sum(row[key] for key in (
                "subscription_gbp", "university_license_gbp", "voucher_gbp",
                "merchant_ads_gbp", "bank_referral_gbp", "savings_gbp",
            )))


if __name__ == "__main__":
    unittest.main()
