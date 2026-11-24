"""Employer pilot and annual licence cohorts for the investor-growth forecast."""
from collections import Counter


def project_employers(inputs, months, service_cost, bank_cost):
    pilot = inputs["pilot"]
    plans = inputs["annual_plans"]
    term = inputs["licence_months"]
    discount = inputs["conversion_discount_gbp"]
    registered_share = inputs["registration_share"]
    active_share = inputs["monthly_active_share"]
    if not 0 <= active_share <= registered_share <= 1:
        raise ValueError("Employer activation must be within registered and eligible employees")
    if discount < 0 or discount > min(plan["gbp"] for plan in plans.values()):
        raise ValueError("Employer conversion discount must fit all annual plans")
    if term <= 0 or pilot["months"] <= 0:
        raise ValueError("Employer contract terms must be positive")
    for cohort in inputs["cohorts"]:
        plan = cohort.get("annual_plan")
        if (plan is not None and plan not in plans) or cohort["pilot_month"] < 1 or cohort["renewals"] < 0:
            raise ValueError("Employer cohort must specify a valid plan, pilot month and renewal count")
    rows = []
    cumulative_seats = 0
    deferred = 0
    for month in range(1, months + 1):
        starts = Counter()
        active = Counter()
        standard_arr = discounted_arr = cash = seats = 0
        for cohort in inputs["cohorts"]:
            first = cohort["pilot_month"]
            conversion = first + pilot["months"]
            plan_name = cohort.get("annual_plan")
            plan = plans.get(plan_name) if plan_name else None
            if first == month:
                starts["pilot"] += 1
                cash += pilot["gbp"]
                cumulative_seats += pilot["capacity"]
            if first <= month < conversion:
                active["pilot"] += 1
                seats += pilot["capacity"]
            if plan is None:
                continue
            age = month - conversion
            if age == 0:
                starts[plan_name] += 1
                cumulative_seats += max(0, plan["capacity"] - pilot["capacity"])
            if 0 <= age < term * (1 + cohort["renewals"]):
                active[plan_name] += 1
                seats += plan["capacity"]
                standard_arr += plan["gbp"]
                discounted_arr += plan["gbp"] - (discount if age < term else 0)
                if age % term == 0:
                    cash += plan["gbp"] - (discount if age == 0 else 0)
        pilot_revenue = active["pilot"] * pilot["gbp"] / pilot["months"]
        licence_revenue = standard_arr / term
        monthly_discount = (standard_arr - discounted_arr) / term
        revenue = pilot_revenue + licence_revenue - monthly_discount
        deferred += cash - revenue
        employees = seats * active_share
        rows.append(dict(
            employer_new_pilots=starts["pilot"],
            employer_new_annual_clients=sum(starts.values()) - starts["pilot"],
            employer_active_pilots=active["pilot"],
            employer_active_clients=sum(active.values()),
            employer_annual_clients=sum(active.values()) - active["pilot"],
            employer_starter_clients=active["Starter"], employer_growth_clients=active["Growth"],
            employer_enterprise_clients=active["Enterprise"],
            employer_annual_eligible_employees=seats - active["pilot"] * pilot["capacity"],
            employer_eligible_employees=seats,
            employer_registered_employees=seats * registered_share,
            employer_cumulative_registered_employees=cumulative_seats * registered_share,
            employer_active_employees=employees,
            employer_cumulative_paid_clients=sum(c["pilot_month"] <= month for c in inputs["cohorts"]),
            employer_pilot_revenue_gbp=pilot_revenue,
            employer_licence_revenue_gbp=licence_revenue,
            employer_conversion_discount_gbp=monthly_discount,
            employer_revenue_gbp=revenue,
            employer_arr_standard_gbp=standard_arr,
            employer_arr_contracted_gbp=discounted_arr,
            employer_cash_receipts_gbp=cash,
            employer_deferred_revenue_gbp=deferred,
            employer_service_gbp=employees * service_cost,
            employer_bank_usage_gbp=employees * bank_cost,
        ))
    for year, budget in enumerate(inputs["annual_budget_gbp"]):
        year_rows = rows[year * 12:(year + 1) * 12]
        spend_start = max(year * 12 + 1, inputs["outreach_month"])
        spend_months = max(1, (year + 1) * 12 + 1 - spend_start)
        client_months = sum(row["employer_active_clients"] for row in year_rows)
        for month, row in enumerate(year_rows, year * 12 + 1):
            fixed = {f"employer_{key}_gbp": amount / spend_months if month >= spend_start else 0
                     for key, amount in budget.items() if key != "onboarding_support"}
            onboarding = budget["onboarding_support"] * row["employer_active_clients"] / client_months if client_months else 0
            row.update(fixed, employer_fixed_budget_gbp=sum(fixed.values()),
                       employer_onboarding_support_gbp=onboarding)
    return rows


def employer_contribution(employer, baseline_bank_spend, minimum):
    bank = employer["employer_bank_usage_gbp"]
    minimum_credit = max(0, minimum - baseline_bank_spend) - max(0, minimum - baseline_bank_spend - bank)
    incremental_direct = employer["employer_service_gbp"] - minimum_credit
    contribution = employer["employer_revenue_gbp"] - incremental_direct
    return dict(employer_incremental_direct_gbp=incremental_direct,
                employer_direct_contribution_gbp=contribution,
                employer_operating_contribution_gbp=contribution - employer["employer_fixed_budget_gbp"]
                - employer["employer_onboarding_support_gbp"])
