"""
Builds the star schema (fact_contacts, dim_customer_profile, dim_campaign,
dim_economic_context) from the raw bank-additional-full.csv flat file.

Design decisions (see README.md and project discussion for rationale):
- dim_customer_profile is a deduplicated demographic-profile dimension, not a
  real customer master -- the source data has no customer ID.
- dim_campaign dedupes (contact, poutcome, pdays, previous); pdays keeps its
  raw 999 sentinel plus a derived was_previously_contacted flag.
- dim_economic_context is keyed by its own surrogate key from the unique
  5-column economic snapshot combinations, not by month -- month and the
  economic snapshot are different grains in this data (no year column exists
  to reconcile them).
- month/day_of_week stay as degenerate attributes on fact_contacts.
- duration is carried onto fact_contacts labeled clearly as benchmark-only;
  it must never be used as a targeting/predictive feature (see README).
- 12 exact duplicate raw rows are dropped before modeling.
"""

import pandas as pd

RAW_PATH = "../bank-data/raw/bank-additional-full.csv"
OUT_DIR = "../bank-data/star_schema"

AGE_BINS = [17, 25, 35, 45, 55, 65, 100]
AGE_LABELS = ["18-25", "26-35", "36-45", "46-55", "56-65", "66+"]


def load_raw() -> pd.DataFrame:
    df = pd.read_csv(RAW_PATH, sep=";")
    before = len(df)
    df = df.drop_duplicates().reset_index(drop=True)
    dropped = before - len(df)
    print(f"loaded {before} raw rows, dropped {dropped} exact duplicates -> {len(df)} rows")
    return df


def build_dim_customer_profile(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    profile = df[["age", "job", "marital", "education", "default", "housing", "loan"]].copy()
    profile["age_bracket"] = pd.cut(profile["age"], bins=AGE_BINS, labels=AGE_LABELS)

    dim = profile.drop_duplicates().reset_index(drop=True)
    dim.insert(0, "customer_profile_key", dim.index + 1)

    dim = dim.rename(
        columns={
            "marital": "marital_status",
            "default": "has_credit_default",
            "housing": "has_housing_loan",
            "loan": "has_personal_loan",
        }
    )
    dim = dim[
        [
            "customer_profile_key",
            "age",
            "age_bracket",
            "job",
            "marital_status",
            "education",
            "has_credit_default",
            "has_housing_loan",
            "has_personal_loan",
        ]
    ]

    join_cols = ["age", "age_bracket", "job", "marital", "education", "default", "housing", "loan"]
    lookup = profile.merge(
        dim.rename(
            columns={
                "marital_status": "marital",
                "has_credit_default": "default",
                "has_housing_loan": "housing",
                "has_personal_loan": "loan",
            }
        ),
        on=join_cols,
        how="left",
    )
    return dim, lookup["customer_profile_key"]


def build_dim_campaign(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    camp = df[["contact", "poutcome", "pdays", "previous"]].copy()
    camp["was_previously_contacted"] = camp["pdays"] != 999

    dim = camp.drop_duplicates().reset_index(drop=True)
    dim.insert(0, "campaign_key", dim.index + 1)
    dim = dim.rename(
        columns={
            "contact": "contact_type",
            "poutcome": "previous_outcome",
            "pdays": "days_since_previous_contact",
            "previous": "previous_contacts_count",
        }
    )
    dim = dim[
        [
            "campaign_key",
            "contact_type",
            "previous_outcome",
            "days_since_previous_contact",
            "was_previously_contacted",
            "previous_contacts_count",
        ]
    ]

    join_cols = ["contact", "poutcome", "pdays", "was_previously_contacted", "previous"]
    lookup = camp.merge(
        dim.rename(
            columns={
                "contact_type": "contact",
                "previous_outcome": "poutcome",
                "days_since_previous_contact": "pdays",
                "previous_contacts_count": "previous",
            }
        ),
        on=join_cols,
        how="left",
    )
    return dim, lookup["campaign_key"]


def build_dim_economic_context(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    econ_cols = ["emp.var.rate", "cons.price.idx", "cons.conf.idx", "euribor3m", "nr.employed"]
    econ = df[econ_cols].copy()

    dim = econ.drop_duplicates().reset_index(drop=True)
    dim.insert(0, "economic_context_key", dim.index + 1)
    dim = dim.rename(
        columns={
            "emp.var.rate": "emp_var_rate",
            "cons.price.idx": "cons_price_idx",
            "cons.conf.idx": "cons_conf_idx",
            "nr.employed": "nr_employed",
        }
    )

    lookup = econ.merge(
        dim.rename(
            columns={
                "emp_var_rate": "emp.var.rate",
                "cons_price_idx": "cons.price.idx",
                "cons_conf_idx": "cons.conf.idx",
                "nr_employed": "nr.employed",
            }
        ),
        on=econ_cols,
        how="left",
    )
    return dim, lookup["economic_context_key"]


def build_fact_contacts(
    df: pd.DataFrame,
    customer_profile_key: pd.Series,
    campaign_key: pd.Series,
    economic_context_key: pd.Series,
) -> pd.DataFrame:
    fact = pd.DataFrame(
        {
            "contact_key": df.index + 1,
            "customer_profile_key": customer_profile_key.values,
            "campaign_key": campaign_key.values,
            "economic_context_key": economic_context_key.values,
            "month": df["month"],
            "day_of_week": df["day_of_week"],
            "campaign_number": df["campaign"],
            "duration_seconds": df["duration"],  # benchmark-only, see README
            "converted": df["y"].eq("yes"),
        }
    )
    return fact


def main() -> None:
    df = load_raw()

    dim_customer_profile, customer_profile_key = build_dim_customer_profile(df)
    dim_campaign, campaign_key = build_dim_campaign(df)
    dim_economic_context, economic_context_key = build_dim_economic_context(df)
    fact_contacts = build_fact_contacts(df, customer_profile_key, campaign_key, economic_context_key)

    print(f"dim_customer_profile: {len(dim_customer_profile)} rows")
    print(f"dim_campaign: {len(dim_campaign)} rows")
    print(f"dim_economic_context: {len(dim_economic_context)} rows")
    print(f"fact_contacts: {len(fact_contacts)} rows")

    assert fact_contacts["customer_profile_key"].notna().all()
    assert fact_contacts["campaign_key"].notna().all()
    assert fact_contacts["economic_context_key"].notna().all()

    dim_customer_profile.to_csv(f"{OUT_DIR}/dim_customer_profile.csv", index=False)
    dim_campaign.to_csv(f"{OUT_DIR}/dim_campaign.csv", index=False)
    dim_economic_context.to_csv(f"{OUT_DIR}/dim_economic_context.csv", index=False)
    fact_contacts.to_csv(f"{OUT_DIR}/fact_contacts.csv", index=False)
    print(f"wrote all 4 tables to {OUT_DIR}/")


if __name__ == "__main__":
    main()
