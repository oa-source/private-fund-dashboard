import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Private Funds Market Map Intelligence",
    page_icon="🏦",
    layout="wide",
)


# ============================================================
# PROJECT PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
PDF_DIR = BASE_DIR / "pdfs"
DATA_DIR = BASE_DIR / "data"

EXTRACT_SCRIPT = BASE_DIR / "extract_pdfs_to_database.py"

# Main dashboard uses approved rows only
EXTRACTED_FUNDS_CSV = DATA_DIR / "funds_approved_master.csv"

# Fallback files
CLEAN_FUNDS_CSV = DATA_DIR / "clean_funds_master.csv"
RAW_EXTRACTED_FUNDS_CSV = DATA_DIR / "extracted_funds.csv"

FUNDRAISING_TIMELINE_CSV = DATA_DIR / "fundraising_timeline.csv"
FUTURE_FUNDRAISING_CSV = DATA_DIR / "future_fundraising_timeline.csv"
SOURCE_FILES_CSV = DATA_DIR / "source_files.csv"

# Review tab uses separated needs-review rows
NEEDS_REVIEW_CSV = DATA_DIR / "funds_needs_review.csv"


# ============================================================
# BASIC HELPERS
# ============================================================

CITY_PREFIXES = [
    "amsterdam",
    "antwerp",
    "brussels",
    "london",
    "luxembourg",
    "naarden",
    "the hague",
    "utrecht",
    "veenendaal",
    "russels",
    "ussum",
    "bussum",
    "diegen",
    "diegem",
    "houten",
]

ROMAN_NUMERALS = {
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII",
    "XIX", "XX", "XXI", "XXII", "XXIII", "XXIV", "XXV",
}


def clean_text(value):
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    value = str(value)
    value = value.replace("\u00a0", " ")
    value = value.replace("–", "-")
    value = value.replace("—", "-")
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def clean_basic_name(value):
    name = clean_text(value)

    name = re.sub(r"^[\*\•\·\-\–\—\|\s]+", "", name).strip()
    name = name.replace("**", "").strip()
    name = re.sub(r"\s*\($", "", name).strip()
    name = re.sub(r"[\|\;\,]+$", "", name).strip()

    return clean_text(name)


def remove_city_prefix(value):
    name = clean_basic_name(value)
    lower = name.lower()

    for city in CITY_PREFIXES:
        prefix = city + " "
        if lower.startswith(prefix):
            name = name[len(prefix):].strip()
            break

    return clean_basic_name(name)


def clean_manager_name_value(value):
    manager = remove_city_prefix(value)

    for prefix in ["A ", "An ", "The "]:
        if manager.lower().startswith(prefix.lower()):
            candidate = manager[len(prefix):].strip()

            if len(candidate.split()) >= 2:
                manager = candidate
                break

    return clean_basic_name(manager)


def is_generic_short_fund_name(name):
    name = clean_basic_name(name)

    if not name:
        return True

    upper = name.upper()

    if upper in ROMAN_NUMERALS:
        return True

    bad_patterns = [
        r"Fund\s+[IVXLCDM]+",
        r"Partners\s+[IVXLCDM]+",
        r"Investors\s+[IVXLCDM]+",
        r"Equity\s+Fund\s+[IVXLCDM]+",
        r"Capital\s+Fund\s+[IVXLCDM]+",
        r"Growth\s+[IVXLCDM]+",
        r"Buyout\s+[IVXLCDM]+",
        r"Infrastructure\s+[IVXLCDM]+",
    ]

    for pattern in bad_patterns:
        if re.fullmatch(pattern, name, flags=re.IGNORECASE):
            return True

    if len(name) <= 4:
        return True

    return False


def clean_fund_name_from_row(row):
    fund = remove_city_prefix(row.get("fund_name", ""))
    manager = remove_city_prefix(row.get("manager_name", ""))

    fund = clean_basic_name(fund)
    manager = clean_basic_name(manager)

    # Do NOT remove normal manager prefixes from full fund names.
    # Keep examples like:
    # Andreessen Horowitz a16z AI Fund
    # Brookfield Global Transition Fund 2
    # Warburg Pincus Global Growth 14
    # Ardian Infrastructure Fund VI

    # Only remove true repeated duplicate phrases:
    # GTCR GTCR Fund XIV -> GTCR Fund XIV
    # KKR KKR Ascendant -> KKR Ascendant
    words = fund.split()

    for n in [5, 4, 3, 2, 1]:
        if len(words) >= n * 2:
            first = " ".join(words[:n]).lower()
            second = " ".join(words[n:n * 2]).lower()

            if first == second:
                fund = " ".join(words[n:])
                break

    fund = clean_basic_name(fund)

    # Remove bad leading "I " when it is clearly a broken extraction.
    if fund.upper().startswith("I "):
        candidate = fund[2:].strip()

        if len(candidate.split()) >= 2:
            fund = candidate

    # Run duplicate cleanup again after removing leading "I "
    words = fund.split()

    for n in [5, 4, 3, 2, 1]:
        if len(words) >= n * 2:
            first = " ".join(words[:n]).lower()
            second = " ".join(words[n:n * 2]).lower()

            if first == second:
                fund = " ".join(words[n:])
                break

    fund = clean_basic_name(fund)

    # If the fund name is still useless, add manager.
    if manager and is_generic_short_fund_name(fund):
        fund = f"{manager} {fund}".strip()

    return clean_basic_name(fund)


def fix_size_numeric(value):
    try:
        value = float(value)
    except Exception:
        return None

    if pd.isna(value):
        return None

    while value > 20_000:
        value = value / 1000

    return round(value, 2)


def currency_symbol(currency):
    currency = clean_text(currency).upper()

    if currency == "EUR":
        return "€"

    if currency == "USD":
        return "$"

    if currency == "GBP":
        return "£"

    if currency == "CAD":
        return "CAD "

    if currency == "AUD":
        return "AUD "

    return ""


def format_fund_size_from_values(size_millions, currency):
    symbol = currency_symbol(currency)

    try:
        size_millions = float(size_millions)
    except Exception:
        return ""

    if pd.isna(size_millions):
        return ""

    if size_millions < 1000:
        return f"{symbol}{size_millions:,.0f}m"

    return f"{symbol}{size_millions / 1000:,.1f}bn"


def format_total_size(value_millions):
    try:
        value_millions = float(value_millions)
    except Exception:
        return "N/A"

    if pd.isna(value_millions):
        return "N/A"

    if value_millions >= 1000:
        return f"{value_millions / 1000:,.1f}bn"

    return f"{value_millions:,.0f}m"


def short_label(text, max_chars=55):
    text = clean_text(text)

    if len(text) <= max_chars:
        return text

    return text[:max_chars].rstrip() + "..."


def make_clean_view_flag(value):
    value = clean_text(value).lower()

    if "needs_review" in value:
        return "Needs review"

    return "Cleaner row"


# ============================================================
# DATA LOADING / CLEANING
# ============================================================

def clean_dataframe(df):
    if df.empty:
        return df

    expected_cols = [
        "fund_id",
        "fund_name",
        "manager_name",
        "fund_size",
        "fund_size_numeric",
        "fund_size_currency",
        "vintage_year",
        "fundraising_year",
        "fundraising_status",
        "timeline_bucket",
        "strategy",
        "sub_strategy",
        "geography",
        "region",
        "sector_focus",
        "source_name",
        "source_file",
        "source_page",
        "raw_text",
        "data_quality_flag",
        "created_at",
        "fund_size_display",
        "fund_size_millions",
    ]

    for col in expected_cols:
        if col not in df.columns:
            df[col] = ""

    numeric_cols = [
        "fund_size_numeric",
        "fund_size_millions",
        "vintage_year",
        "fundraising_year",
        "source_page",
    ]

    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["fund_size_numeric"] = df["fund_size_numeric"].apply(fix_size_numeric)

    df["fund_size_millions"] = df["fund_size_millions"].fillna(df["fund_size_numeric"])
    df["fund_size_millions"] = df["fund_size_millions"].apply(fix_size_numeric)

    text_cols = [
        "fund_id",
        "fund_name",
        "manager_name",
        "fund_size",
        "fund_size_currency",
        "fundraising_status",
        "timeline_bucket",
        "strategy",
        "sub_strategy",
        "geography",
        "region",
        "sector_focus",
        "source_name",
        "source_file",
        "raw_text",
        "data_quality_flag",
        "created_at",
        "fund_size_display",
    ]

    for col in text_cols:
        df[col] = df[col].fillna("").astype(str)

    df["manager_name"] = df["manager_name"].apply(clean_manager_name_value)
    df["fund_name"] = df.apply(clean_fund_name_from_row, axis=1)

    df["fund_size_millions"] = df["fund_size_numeric"]

    df["fund_size_display"] = df.apply(
        lambda row: format_fund_size_from_values(
            row.get("fund_size_millions"),
            row.get("fund_size_currency"),
        ),
        axis=1,
    )

    df.loc[
        df["fund_size_display"].astype(str).str.strip() == "",
        "fund_size_display",
    ] = df["fund_size"]

    df["data_quality_view"] = df["data_quality_flag"].apply(make_clean_view_flag)

    df["timeline_year"] = pd.to_numeric(
        df["fundraising_year"].fillna(df["vintage_year"]),
        errors="coerce",
    )

    df["fund_label_short"] = df["fund_name"].apply(lambda x: short_label(x, 55))
    df["manager_label_short"] = df["manager_name"].apply(lambda x: short_label(x, 35))

    df["manager_fund_label"] = (
        df["manager_label_short"].astype(str)
        + " — "
        + df["fund_label_short"].astype(str)
    )

    return df


@st.cache_data
def load_data():
    if EXTRACTED_FUNDS_CSV.exists():
        funds = pd.read_csv(EXTRACTED_FUNDS_CSV)
    elif CLEAN_FUNDS_CSV.exists():
        funds = pd.read_csv(CLEAN_FUNDS_CSV)
    elif RAW_EXTRACTED_FUNDS_CSV.exists():
        funds = pd.read_csv(RAW_EXTRACTED_FUNDS_CSV)
    else:
        funds = pd.DataFrame()

    if FUNDRAISING_TIMELINE_CSV.exists():
        timeline = pd.read_csv(FUNDRAISING_TIMELINE_CSV)
    else:
        timeline = pd.DataFrame()

    if SOURCE_FILES_CSV.exists():
        sources = pd.read_csv(SOURCE_FILES_CSV)
    else:
        sources = pd.DataFrame()

    if NEEDS_REVIEW_CSV.exists():
        needs_review = pd.read_csv(NEEDS_REVIEW_CSV)
    else:
        needs_review = pd.DataFrame()

    funds = clean_dataframe(funds)
    timeline = clean_dataframe(timeline)
    needs_review = clean_dataframe(needs_review)

    return funds, timeline, sources, needs_review


def run_extraction_script():
    result = subprocess.run(
        [sys.executable, str(EXTRACT_SCRIPT)],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
    )

    return result.returncode, result.stdout, result.stderr


# ============================================================
# FILTERING
# ============================================================

def apply_common_filters(df, filters):
    filtered = df.copy()

    if filtered.empty:
        return filtered

    if filters["strategies"]:
        filtered = filtered[filtered["strategy"].isin(filters["strategies"])]

    if filters["geographies"]:
        filtered = filtered[filtered["geography"].isin(filters["geographies"])]

    if filters["sub_strategies"]:
        filtered = filtered[filtered["sub_strategy"].isin(filters["sub_strategies"])]

    if filters["regions"]:
        filtered = filtered[filtered["region"].isin(filters["regions"])]

    if filters["statuses"]:
        filtered = filtered[filtered["fundraising_status"].isin(filters["statuses"])]

    if filters["sources"]:
        filtered = filtered[filtered["source_file"].isin(filters["sources"])]

    if filters["qualities"]:
        filtered = filtered[filtered["data_quality_view"].isin(filters["qualities"])]

    if filters["min_size"] > 0:
        filtered = filtered[filtered["fund_size_numeric"].fillna(-1) >= filters["min_size"]]

    if filters["max_size"] > 0:
        filtered = filtered[filtered["fund_size_numeric"].fillna(999999999) <= filters["max_size"]]

    if filters["years"]:
        filtered = filtered[filtered["timeline_year"].isin(filters["years"])]

    if filters["search_text"]:
        search = filters["search_text"].lower().strip()
        filtered = filtered[
            filtered["fund_name"].str.lower().str.contains(search, na=False)
            | filtered["manager_name"].str.lower().str.contains(search, na=False)
        ]

    return filtered


# ============================================================
# CHART HELPERS
# ============================================================

def make_count_by_year_chart(df, title):
    chart_df = df.copy()
    chart_df = chart_df[chart_df["timeline_year"].notna()].copy()

    if chart_df.empty:
        return None

    chart_df["timeline_year"] = chart_df["timeline_year"].astype(int).astype(str)

    grouped = (
        chart_df.groupby("timeline_year")
        .size()
        .reset_index(name="fund_count")
        .sort_values("timeline_year")
    )

    fig = px.bar(
        grouped,
        x="timeline_year",
        y="fund_count",
        text="fund_count",
        title=title,
        labels={
            "timeline_year": "Year",
            "fund_count": "Fund rows",
        },
    )

    fig.update_traces(
        texttemplate="%{text:,.0f}",
        textposition="outside",
        marker_line_width=0,
    )

    fig.update_layout(
        height=420,
        showlegend=False,
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=40, r=30, t=70, b=50),
        xaxis=dict(title="Year", type="category"),
        yaxis=dict(
            title="Fund rows",
            rangemode="tozero",
            tickmode="auto",
            nticks=8,
        ),
    )

    return fig


def make_clean_timeline_chart(df, title, max_rows=75):
    chart_data = df.copy()
    chart_data = chart_data[chart_data["timeline_year"].notna()].copy()

    if chart_data.empty:
        return None

    chart_data["timeline_year"] = chart_data["timeline_year"].astype(int)

    chart_data = chart_data.sort_values(
        by=["timeline_year", "fund_size_numeric"],
        ascending=[True, False],
    ).head(max_rows)

    chart_data["hover_text"] = (
        "Fund: " + chart_data["fund_name"].astype(str)
        + "<br>Manager: " + chart_data["manager_name"].astype(str)
        + "<br>Fund size: " + chart_data["fund_size_display"].astype(str)
        + "<br>Year: " + chart_data["timeline_year"].astype(str)
        + "<br>Status: " + chart_data["fundraising_status"].astype(str)
        + "<br>Strategy: " + chart_data["strategy"].astype(str)
        + "<br>Geography: " + chart_data["geography"].astype(str)
        + "<br>Source: " + chart_data["source_file"].astype(str)
        + "<br>Page: " + chart_data["source_page"].astype(str)
    )

    fig = go.Figure()

    strategies = sorted([x for x in chart_data["strategy"].dropna().unique() if clean_text(x)])

    if not strategies:
        strategies = [""]

    for strategy in strategies:
        strategy_df = chart_data[chart_data["strategy"] == strategy].copy()

        fig.add_trace(
            go.Bar(
                x=[1] * len(strategy_df),
                y=strategy_df["manager_fund_label"],
                base=strategy_df["timeline_year"],
                orientation="h",
                name=strategy if strategy else "unknown",
                text=strategy_df["fund_size_display"],
                hovertext=strategy_df["hover_text"],
                hoverinfo="text",
            )
        )

    fig.update_layout(
        title=title,
        xaxis_title="Fundraising / vintage year",
        yaxis_title="Fund",
        barmode="stack",
        height=850,
        legend_title="Strategy",
        margin=dict(l=20, r=20, t=60, b=40),
    )

    fig.update_yaxes(autorange="reversed")

    return fig


def make_top_managers_chart(df):
    chart_df = df.copy()

    grouped = (
        chart_df.groupby("manager_name")
        .agg(
            fund_count=("fund_name", "count"),
            total_size_millions=("fund_size_numeric", "sum"),
        )
        .reset_index()
    )

    grouped = grouped[grouped["manager_name"].astype(str).str.len() > 0].copy()
    grouped = grouped.sort_values("total_size_millions", ascending=False).head(20)

    if grouped.empty:
        return None

    fig = px.bar(
        grouped,
        x="total_size_millions",
        y="manager_name",
        orientation="h",
        text="total_size_millions",
        title="Top managers by total fund size found",
        labels={
            "total_size_millions": "Total fund size found (€m)",
            "manager_name": "Manager",
        },
        hover_data={
            "fund_count": True,
            "total_size_millions": ":,.0f",
        },
    )

    fig.update_traces(
        texttemplate="€%{text:,.0f}m",
        textposition="outside",
        marker_line_width=0,
    )

    fig.update_yaxes(autorange="reversed")
    fig.update_layout(
        height=650,
        showlegend=False,
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=40, r=30, t=70, b=50),
        xaxis=dict(
            title="Total fund size found (€m)",
            tickprefix="€",
            ticksuffix="m",
            separatethousands=True,
        ),
        yaxis=dict(title=""),
    )

    return fig


# ============================================================
# LOAD DATA
# ============================================================

funds_df, timeline_df, sources_df, needs_review_df = load_data()


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title("Private Funds Market Map")

st.sidebar.markdown("### Add new PDFs")

uploaded_files = st.sidebar.file_uploader(
    "Drop new market map PDFs here",
    type=["pdf"],
    accept_multiple_files=True,
)

if uploaded_files:
    PDF_DIR.mkdir(exist_ok=True)

    saved_count = 0

    for uploaded_file in uploaded_files:
        save_path = PDF_DIR / uploaded_file.name

        with open(save_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        saved_count += 1

    st.sidebar.success(f"Saved {saved_count} PDF(s) into the pdfs folder.")

if st.sidebar.button("Process PDFs / Update Database"):
    with st.spinner("Processing PDFs and updating the database..."):
        return_code, stdout, stderr = run_extraction_script()

    if return_code == 0:
        st.sidebar.success("Database updated.")
        st.sidebar.text_area("Extraction output", stdout, height=300)
        st.cache_data.clear()
        st.rerun()
    else:
        st.sidebar.error("Extraction failed.")
        st.sidebar.text_area("Error output", stderr, height=300)


# ============================================================
# MAIN HEADER
# ============================================================

st.title("Private Funds Market Map Intelligence Dashboard")
st.caption(
    "Upload market map PDFs, extract fund data, track fundraising timelines, and review manager profiles."
)

if funds_df.empty:
    st.warning("No approved fund data found yet. Run quality_pipeline.py first.")
    st.stop()


# ============================================================
# TOP METRICS
# ============================================================

total_rows = len(funds_df)
clean_rows = len(funds_df[funds_df["data_quality_view"] == "Cleaner row"])
source_count = funds_df["source_file"].nunique()
manager_count = funds_df["manager_name"].nunique()
total_size_millions = funds_df["fund_size_numeric"].dropna().sum()
review_rows = len(needs_review_df)

m1, m2, m3, m4, m5, m6 = st.columns(6)

with m1:
    st.metric("Approved fund rows", f"{total_rows:,}")

with m2:
    st.metric("Cleaner rows", f"{clean_rows:,}")

with m3:
    st.metric("Managers", f"{manager_count:,}")

with m4:
    st.metric("Source PDFs", f"{source_count:,}")

with m5:
    st.metric("Total fund size found", format_total_size(total_size_millions))

with m6:
    st.metric("Rows needing review", f"{review_rows:,}")


# ============================================================
# FILTER OPTIONS
# ============================================================

strategy_options = sorted([x for x in funds_df["strategy"].unique() if clean_text(x)])
geography_options = sorted([x for x in funds_df["geography"].unique() if clean_text(x)])
sub_strategy_options = sorted([x for x in funds_df["sub_strategy"].unique() if clean_text(x)])
region_options = sorted([x for x in funds_df["region"].unique() if clean_text(x)])
status_options = sorted([x for x in funds_df["fundraising_status"].unique() if clean_text(x)])
source_options = sorted([x for x in funds_df["source_file"].unique() if clean_text(x)])
year_options = sorted([int(x) for x in funds_df["timeline_year"].dropna().unique()])


with st.expander("Filters", expanded=True):
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        selected_strategies = st.multiselect(
            "Strategy",
            options=strategy_options,
            default=[],
            help="Select one or multiple strategies. Leave empty to show all.",
        )

    with c2:
        selected_geographies = st.multiselect(
            "Geography",
            options=geography_options,
            default=[],
            help="Select one or multiple geographies. Leave empty to show all.",
        )

    with c3:
        selected_sub_strategies = st.multiselect(
            "Sub-strategy / sector",
            options=sub_strategy_options,
            default=[],
            help="Select one or multiple sub-strategies. Leave empty to show all.",
        )

    with c4:
        selected_regions = st.multiselect(
            "Region",
            options=region_options,
            default=[],
            help="Select one or multiple regions. Leave empty to show all.",
        )

    c5, c6, c7, c8 = st.columns(4)

    with c5:
        selected_statuses = st.multiselect(
            "Fundraising status",
            options=status_options,
            default=[],
            help="Select one or multiple statuses. Leave empty to show all.",
        )

    with c6:
        selected_years = st.multiselect(
            "Fundraising / vintage year",
            options=year_options,
            default=[],
            help="Select one or multiple years. Leave empty to show all years.",
        )

    with c7:
        min_size = st.number_input(
            "Minimum fund size, in millions",
            min_value=0.0,
            value=0.0,
            step=50.0,
            help="Example: 500 = funds above €/$/£500m.",
        )

    with c8:
        max_size = st.number_input(
            "Maximum fund size, in millions",
            min_value=0.0,
            value=0.0,
            step=50.0,
            help="Example: 2000 = funds below €/$/£2.0bn.",
        )

    c9, c10, c11 = st.columns([1, 1, 2])

    with c9:
        selected_qualities = st.multiselect(
            "Data quality",
            options=["Cleaner row", "Needs review"],
            default=["Cleaner row"],
            help="Select one or multiple data-quality types.",
        )

    with c10:
        selected_sources = st.multiselect(
            "Source PDF",
            options=source_options,
            default=[],
            help="Select one or multiple PDFs. Leave empty to show all.",
        )

    with c11:
        search_text = st.text_input("Search fund or manager")


filters = {
    "strategies": selected_strategies,
    "geographies": selected_geographies,
    "sub_strategies": selected_sub_strategies,
    "regions": selected_regions,
    "statuses": selected_statuses,
    "years": selected_years,
    "qualities": selected_qualities,
    "sources": selected_sources,
    "min_size": min_size,
    "max_size": max_size,
    "search_text": search_text,
}

filtered_df = apply_common_filters(funds_df, filters)


# ============================================================
# TABS
# ============================================================

tab1, tab2, tab3, tab4 = st.tabs(
    [
        "Market Overview",
        "Fundraising Timeline",
        "Manager Profiles",
        "Sources & Data Quality",
    ]
)


# ============================================================
# TAB 1 — MARKET OVERVIEW
# ============================================================

with tab1:
    st.header("Market Overview")

    st.write(f"Showing **{len(filtered_df):,}** approved fund rows after filters.")

    if filtered_df.empty:
        st.warning("No rows match the current filters.")
    else:
        chart_df = filtered_df.copy()

        chart_df["chart_year"] = chart_df["fundraising_year"]
        chart_df["chart_year"] = chart_df["chart_year"].fillna(chart_df["vintage_year"])

        chart_df["chart_year"] = pd.to_numeric(chart_df["chart_year"], errors="coerce")
        chart_df["fund_size_millions"] = pd.to_numeric(chart_df["fund_size_millions"], errors="coerce")

        chart_df = chart_df.dropna(subset=["chart_year", "fund_size_millions"]).copy()
        chart_df["chart_year"] = chart_df["chart_year"].astype(int).astype(str)

        if len(chart_df) < 3:
            st.info("Not enough fund rows for useful charts. Add more PDFs or clear filters.")
        else:
            c1, c2 = st.columns(2)

            with c1:
                year_chart = (
                    chart_df
                    .groupby("chart_year", as_index=False)["fund_size_millions"]
                    .sum()
                    .sort_values("chart_year")
                )

                fig = px.bar(
                    year_chart,
                    x="chart_year",
                    y="fund_size_millions",
                    text="fund_size_millions",
                    title="Total fund size by year",
                    labels={
                        "chart_year": "Year",
                        "fund_size_millions": "Total fund size (€m)",
                    },
                )

                fig.update_traces(
                    texttemplate="€%{text:,.0f}m",
                    textposition="outside",
                    marker_line_width=0,
                )

                fig.update_layout(
                    height=420,
                    showlegend=False,
                    plot_bgcolor="white",
                    paper_bgcolor="white",
                    margin=dict(l=40, r=30, t=70, b=50),
                    title=dict(font=dict(size=20)),
                    xaxis=dict(type="category", title="Year"),
                    yaxis=dict(
                        title="Total fund size (€m)",
                        tickprefix="€",
                        ticksuffix="m",
                        separatethousands=True,
                        rangemode="tozero",
                        tickmode="auto",
                        nticks=8,
                    ),
                )

                st.plotly_chart(fig, width="stretch")

            with c2:
                strategy_chart = (
                    chart_df
                    .groupby("strategy", as_index=False)["fund_size_millions"]
                    .sum()
                    .sort_values("fund_size_millions", ascending=False)
                    .head(10)
                )

                fig = px.bar(
                    strategy_chart,
                    x="fund_size_millions",
                    y="strategy",
                    orientation="h",
                    text="fund_size_millions",
                    title="Fund size by strategy",
                    labels={
                        "fund_size_millions": "Total fund size (€m)",
                        "strategy": "Strategy",
                    },
                )

                fig.update_traces(
                    texttemplate="€%{text:,.0f}m",
                    textposition="outside",
                    marker_line_width=0,
                )

                fig.update_layout(
                    height=420,
                    showlegend=False,
                    plot_bgcolor="white",
                    paper_bgcolor="white",
                    margin=dict(l=40, r=30, t=70, b=50),
                    title=dict(font=dict(size=20)),
                    xaxis=dict(
                        title="Total fund size (€m)",
                        tickprefix="€",
                        ticksuffix="m",
                        separatethousands=True,
                    ),
                    yaxis=dict(title=""),
                )

                st.plotly_chart(fig, width="stretch")

            c3, c4 = st.columns(2)

            with c3:
                fig = make_count_by_year_chart(
                    filtered_df,
                    "Number of approved funds by year",
                )

                if fig is not None:
                    st.plotly_chart(fig, width="stretch")

            with c4:
                fig = make_top_managers_chart(filtered_df)

                if fig is not None:
                    st.plotly_chart(fig, width="stretch")

        st.subheader("Fund database")

        display_cols = [
            "fund_name",
            "manager_name",
            "fund_size_display",
            "fund_size_millions",
            "fund_size_currency",
            "vintage_year",
            "fundraising_year",
            "timeline_bucket",
            "fundraising_status",
            "strategy",
            "sub_strategy",
            "geography",
            "region",
            "source_name",
            "source_file",
            "source_page",
            "data_quality_view",
            "data_quality_flag",
        ]

        display_cols = [col for col in display_cols if col in filtered_df.columns]

        st.dataframe(
            filtered_df[display_cols],
            width="stretch",
            height=500,
        )

        st.download_button(
            "Download filtered data as CSV",
            data=filtered_df[display_cols].to_csv(index=False).encode("utf-8-sig"),
            file_name="filtered_market_map_data.csv",
            mime="text/csv",
        )


# ============================================================
# TAB 2 — TIMELINE
# ============================================================

with tab2:
    st.header("Fundraising Timeline")

    future_file = FUTURE_FUNDRAISING_CSV

    if future_file.exists():
        future_df = pd.read_csv(future_file)

        for col in [
            "fund_name",
            "manager_name",
            "fund_strategy",
            "target_size_display",
            "currency",
            "expected_quarter",
            "status",
            "source_file",
            "raw_line",
        ]:
            if col not in future_df.columns:
                future_df[col] = ""

            future_df[col] = future_df[col].fillna("").astype(str)

        if "target_size_millions" not in future_df.columns:
            future_df["target_size_millions"] = None

        if "expected_year" not in future_df.columns:
            future_df["expected_year"] = None

        if "source_page" not in future_df.columns:
            future_df["source_page"] = ""

        future_df["expected_year"] = pd.to_numeric(
            future_df["expected_year"],
            errors="coerce",
        )

        future_df["target_size_millions"] = pd.to_numeric(
            future_df["target_size_millions"],
            errors="coerce",
        ).apply(fix_size_numeric)

        future_df = future_df[
            future_df["fund_name"].astype(str).str.strip() != ""
        ].copy()

        st.subheader("Future fundraising pipeline")

        if future_df.empty:
            st.info("No clean future fundraising rows found yet.")
        else:
            k1, k2, k3 = st.columns(3)

            with k1:
                st.metric("Future fund rows", f"{len(future_df):,}")

            with k2:
                known_size = future_df["target_size_millions"].dropna().sum()
                st.metric("Known target size", f"€{known_size:,.0f}m")

            with k3:
                st.metric("Source PDFs", f"{future_df['source_file'].nunique():,}")

            chart_df = future_df.copy()

            chart_df["chart_period"] = chart_df["expected_quarter"]

            chart_df.loc[
                chart_df["chart_period"].isna()
                | (chart_df["chart_period"].astype(str).str.strip() == ""),
                "chart_period",
            ] = chart_df["expected_year"].astype("Int64").astype(str)

            chart_df = chart_df[
                chart_df["chart_period"].notna()
                & (chart_df["chart_period"].astype(str) != "<NA>")
                & (chart_df["chart_period"].astype(str).str.strip() != "")
            ].copy()

            future_chart = (
                chart_df
                .groupby("chart_period", as_index=False)
                .agg(
                    fund_count=("fund_name", "count"),
                    target_size_millions=("target_size_millions", "sum"),
                )
                .sort_values("chart_period")
            )

            fig = px.bar(
                future_chart,
                x="chart_period",
                y="fund_count",
                text="fund_count",
                title="Future funds by expected fundraising period",
                labels={
                    "chart_period": "Expected period",
                    "fund_count": "Number of future funds",
                },
            )

            fig.update_traces(
                texttemplate="%{text:,.0f}",
                textposition="outside",
                marker_line_width=0,
            )

            fig.update_layout(
                height=430,
                showlegend=False,
                plot_bgcolor="white",
                paper_bgcolor="white",
                margin=dict(l=40, r=30, t=70, b=50),
                title=dict(font=dict(size=22)),
                xaxis=dict(title="Expected period", type="category"),
                yaxis=dict(
                    title="Number of future funds",
                    rangemode="tozero",
                    tickmode="auto",
                    nticks=8,
                ),
            )

            st.plotly_chart(fig, width="stretch")

            st.subheader("Future fundraising table")

            future_cols = [
                "fund_name",
                "manager_name",
                "fund_strategy",
                "target_size_display",
                "target_size_millions",
                "currency",
                "expected_quarter",
                "expected_year",
                "status",
                "source_file",
                "source_page",
                "raw_line",
            ]

            future_cols = [c for c in future_cols if c in future_df.columns]

            st.dataframe(
                future_df[future_cols],
                width="stretch",
                height=450,
            )

    else:
        st.info("No future fundraising timeline file found. Run extract_future_fundraising.py first.")


# ============================================================
# TAB 3 — MANAGER PROFILES
# ============================================================

with tab3:
    st.header("Manager Profiles")

    manager_list = sorted([x for x in filtered_df["manager_name"].dropna().unique() if clean_text(x)])

    if not manager_list:
        st.warning("No managers available under the current filters.")
    else:
        selected_manager = st.selectbox("Select manager", manager_list)

        manager_df = funds_df[funds_df["manager_name"] == selected_manager].copy()
        manager_clean_df = manager_df[manager_df["data_quality_view"] == "Cleaner row"].copy()

        st.subheader(selected_manager)

        p1, p2, p3, p4, p5 = st.columns(5)

        with p1:
            st.metric("Fund rows found", f"{len(manager_df):,}")

        with p2:
            st.metric("Cleaner rows", f"{len(manager_clean_df):,}")

        with p3:
            st.metric("Strategies", f"{manager_df['strategy'].nunique():,}")

        with p4:
            st.metric("Source PDFs", f"{manager_df['source_file'].nunique():,}")

        with p5:
            manager_size = manager_df["fund_size_numeric"].dropna().sum()
            st.metric("Total size found", format_total_size(manager_size))

        strategy_summary = ", ".join(sorted([x for x in manager_df["strategy"].unique() if clean_text(x)]))
        geography_summary = ", ".join(sorted([x for x in manager_df["geography"].unique() if clean_text(x)]))
        sector_summary = ", ".join(sorted([x for x in manager_df["sub_strategy"].unique() if clean_text(x)]))

        st.markdown("### Summary profile from database")

        st.write(f"**Manager:** {selected_manager}")
        st.write(f"**Strategies found:** {strategy_summary if strategy_summary else 'N/A'}")
        st.write(f"**Geographies found:** {geography_summary if geography_summary else 'N/A'}")
        st.write(f"**Sector / sub-strategy tags:** {sector_summary if sector_summary else 'N/A'}")

        st.markdown("### Plain-English database profile")

        st.info(
            f"{selected_manager} appears in the approved market map database with "
            f"{len(manager_df):,} fund rows. The current profile is based only "
            f"on uploaded PDF evidence. A later upgrade can pull strategy, history, team, "
            f"and investment approach from the manager website."
        )

        st.markdown("### Manager fundraising timeline")

        manager_timeline = manager_df[manager_df["timeline_year"].notna()].copy()

        if not manager_timeline.empty:
            fig = make_clean_timeline_chart(
                manager_timeline,
                f"{selected_manager} fundraising timeline",
                max_rows=100,
            )

            if fig is not None:
                fig.update_layout(height=550)
                st.plotly_chart(fig, width="stretch")

        manager_display_cols = [
            "fund_name",
            "fund_size_display",
            "fund_size_millions",
            "fund_size_currency",
            "vintage_year",
            "fundraising_year",
            "timeline_bucket",
            "fundraising_status",
            "strategy",
            "sub_strategy",
            "geography",
            "region",
            "source_file",
            "source_page",
            "raw_text",
            "data_quality_view",
            "data_quality_flag",
        ]

        manager_display_cols = [col for col in manager_display_cols if col in manager_df.columns]

        st.markdown("### Funds / source evidence for this manager")

        st.dataframe(
            manager_df[manager_display_cols],
            width="stretch",
            height=450,
        )


# ============================================================
# TAB 4 — SOURCES / DATA QUALITY
# ============================================================

with tab4:
    st.header("Sources & Data Quality")

    st.subheader("Processed PDFs")

    if sources_df.empty:
        st.warning("No source file table found.")
    else:
        st.dataframe(sources_df, width="stretch", height=300)

    st.subheader("Rows by source PDF")

    source_counts = (
        funds_df.groupby("source_file")
        .size()
        .reset_index(name="row_count")
        .sort_values("row_count", ascending=False)
    )

    st.dataframe(source_counts, width="stretch", height=300)

    st.subheader("Rows by data quality flag")

    quality_counts = (
        funds_df.groupby("data_quality_flag")
        .size()
        .reset_index(name="row_count")
        .sort_values("row_count", ascending=False)
    )

    st.dataframe(quality_counts, width="stretch", height=300)

    st.subheader("Rows needing review")

    review_cols = [
        "fund_name",
        "manager_name",
        "fund_size_display",
        "fund_size_millions",
        "fund_size_currency",
        "vintage_year",
        "fundraising_year",
        "strategy",
        "geography",
        "source_file",
        "source_page",
        "raw_text",
        "data_quality_view",
        "data_quality_flag",
    ]

    review_cols = [col for col in review_cols if col in needs_review_df.columns]

    if needs_review_df.empty:
        st.info("No needs-review rows found.")
    else:
        st.dataframe(
            needs_review_df[review_cols],
            width="stretch",
            height=500,
        )