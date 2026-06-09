"""
SNF Data Dashboard — Streamlit Multi-State Explorer
Run: streamlit run app.py
"""
import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="SNF State Explorer",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── State list ───────────────────────────────────────────────────────────────
ALL_STATES = {
    "AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California",
    "CO":"Colorado","CT":"Connecticut","DE":"Delaware","FL":"Florida","GA":"Georgia",
    "HI":"Hawaii","ID":"Idaho","IL":"Illinois","IN":"Indiana","IA":"Iowa","KS":"Kansas",
    "KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland","MA":"Massachusetts",
    "MI":"Michigan","MN":"Minnesota","MS":"Mississippi","MO":"Missouri","MT":"Montana",
    "NE":"Nebraska","NV":"Nevada","NH":"New Hampshire","NJ":"New Jersey","NM":"New Mexico",
    "NY":"New York","NC":"North Carolina","ND":"North Dakota","OH":"Ohio","OK":"Oklahoma",
    "OR":"Oregon","PA":"Pennsylvania","RI":"Rhode Island","SC":"South Carolina",
    "SD":"South Dakota","TN":"Tennessee","TX":"Texas","UT":"Utah","VT":"Vermont",
    "VA":"Virginia","WA":"Washington","WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming",
    "DC":"District of Columbia",
}
STATE_KEYS = list(ALL_STATES.keys())

DATA_DIR = Path(__file__).parent

# ── CMS API helpers ──────────────────────────────────────────────────────────
DKAN       = "https://data.cms.gov/provider-data/api/1/datastore/query/4pq5-n9py/0"
DATA_API   = "https://data.cms.gov/data-api/v1/dataset/{id}/data"
LTC_ID     = "129a6503-c0f1-4132-b186-4c0232c2d894"
PBJ_ID     = "7e0d53ba-8f02-4c66-98a5-14a1c997c50d"
COST_ID    = "a69d3df7-3f66-4a0d-b5b8-0d66049bd565"


def _get(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 ** attempt)


def _paginate_dkan(state):
    records, offset = [], 0
    while True:
        params = urllib.parse.urlencode({
            "conditions[0][property]": "state",
            "conditions[0][value]": state,
            "conditions[0][operator]": "=",
            "limit": 500, "offset": offset,
        })
        data = _get(f"{DKAN}?{params}")
        batch = data.get("results", [])
        records.extend(batch)
        offset += len(batch)
        if offset >= data.get("count", 0) or not batch:
            break
    return records


def _paginate_data_api(dataset_id, state_field, state):
    records, offset = [], 0
    while True:
        params = urllib.parse.urlencode(
            {f"filter[{state_field}]": state, "size": 1500, "offset": offset}
        )
        batch = _get(f"{DATA_API.format(id=dataset_id)}?{params}")
        if not isinstance(batch, list) or not batch:
            break
        records.extend(batch)
        if len(batch) < 1500:
            break
        offset += 1500
    return records


# ── Cached data loaders ──────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def load_provider(state_code):
    """Provider Information — primary facility record."""
    # NC fast path: use local CSV if available
    csv_path = DATA_DIR / "nc_snf_provider_info.csv"
    if state_code == "NC" and csv_path.exists():
        df = pd.read_csv(csv_path, dtype=str)
    else:
        raw = _paginate_dkan(state_code)
        df = pd.DataFrame(raw)

    num_cols = [
        "number_of_certified_beds", "average_number_of_residents_per_day",
        "overall_rating", "health_inspection_rating", "qm_rating", "staffing_rating",
        "reported_total_nurse_staffing_hours_per_resident_per_day",
        "reported_rn_staffing_hours_per_resident_per_day",
        "reported_nurse_aide_staffing_hours_per_resident_per_day",
        "reported_lpn_staffing_hours_per_resident_per_day",
        "total_nursing_staff_turnover", "registered_nurse_turnover",
        "number_of_fines", "total_amount_of_fines_in_dollars",
        "total_number_of_penalties", "rating_cycle_1_total_number_of_health_deficiencies",
        "latitude", "longitude",
    ]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df["occupancy_pct"] = (
        df["average_number_of_residents_per_day"] / df["number_of_certified_beds"] * 100
    ).round(1)
    df["ownership_group"] = df["ownership_type"].apply(
        lambda x: "For Profit" if str(x).startswith("For profit")
        else "Non Profit" if str(x).startswith("Non profit")
        else "Government"
    )
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_ltc(state_code):
    """LTC Facility Characteristics — census breakdown & specialized beds."""
    csv_path = DATA_DIR / "nc_ltc_characteristics.csv"
    if state_code == "NC" and csv_path.exists():
        df = pd.read_csv(csv_path, dtype=str)
    else:
        raw = _paginate_data_api(LTC_ID, "State", state_code)
        df = pd.DataFrame(raw) if raw else pd.DataFrame()

    if df.empty:
        return df
    num_cols = [
        "Medicare Census", "Medicaid Census", "Other Census", "Total Residents",
        "Number of Alzheimer's Disease Beds", "Number of Ventilator Beds",
        "Number of Hospice Beds", "Number of Dialysis Beds",
    ]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_cost(state_code):
    """SNF Cost Report — financial and utilization data."""
    csv_path = DATA_DIR / "nc_snf_cost_report.csv"
    if state_code == "NC" and csv_path.exists():
        df = pd.read_csv(csv_path, dtype=str)
    else:
        raw = _paginate_data_api(COST_ID, "State Code", state_code)
        df = pd.DataFrame(raw) if raw else pd.DataFrame()

    if df.empty:
        return df
    num_cols = [
        "Number of Beds", "Total Bed Days Available", "Total Days Total",
        "Total Days Title XVIII", "Total Days Title XIX", "Total Days Other",
        "Total Costs", "Net Patient Revenue", "Net Income",
        "SNF Admissions Total", "SNF Average Length of Stay Title XVIII",
        "Total Salaries From Worksheet A", "Contract Labor",
    ]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "Total Days Total" in df.columns and "Total Bed Days Available" in df.columns:
        df["occupancy_pct"] = (
            df["Total Days Total"] / df["Total Bed Days Available"] * 100
        ).round(1)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_pbj(state_code):
    """PBJ Daily Nurse Staffing — daily census & staffing hours."""
    csv_path = DATA_DIR / "nc_pbj_daily_nurse_staffing_CY2025Q4.csv"
    if state_code == "NC" and csv_path.exists():
        df = pd.read_csv(csv_path, dtype=str)
    else:
        raw = _paginate_data_api(PBJ_ID, "STATE", state_code)
        df = pd.DataFrame(raw) if raw else pd.DataFrame()

    if df.empty:
        return df
    pbj_num = [
        "MDScensus", "Hrs_RN", "Hrs_RNadmin", "Hrs_RNDON",
        "Hrs_LPN", "Hrs_LPNadmin", "Hrs_CNA", "Hrs_CNA_ctr",
        "Hrs_RN_ctr", "Hrs_RNadmin_ctr", "Hrs_RNDON_ctr",
    ]
    for c in pbj_num:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["total_rn"] = df.get("Hrs_RN", 0) + df.get("Hrs_RNadmin", 0) + df.get("Hrs_RNDON", 0)
    df["total_lpn"] = df.get("Hrs_LPN", 0) + df.get("Hrs_LPNadmin", 0)
    df["rn_contract"] = df.get("Hrs_RN_ctr", 0) + df.get("Hrs_RNadmin_ctr", 0) + df.get("Hrs_RNDON_ctr", 0)
    df["cna_contract"] = df.get("Hrs_CNA_ctr", 0)
    if "WorkDate" in df.columns:
        df["WorkDate"] = pd.to_datetime(df["WorkDate"], format="%Y%m%d", errors="coerce")
    return df


# ── Sidebar ──────────────────────────────────────────────────────────────────
st.sidebar.title("🏥 SNF Explorer")
selected_state = st.sidebar.selectbox(
    "State",
    options=STATE_KEYS,
    format_func=lambda x: f"{x} — {ALL_STATES[x]}",
    index=STATE_KEYS.index("NC"),
)
state_name = ALL_STATES[selected_state]

# Load data with spinner
with st.spinner(f"Loading {state_name} data..."):
    provider = load_provider(selected_state)

st.sidebar.divider()
st.sidebar.subheader("Filters")

counties = sorted(provider["countyparish"].dropna().unique()) if "countyparish" in provider.columns else []
sel_counties = st.sidebar.multiselect("County / Region", counties)
sel_own = st.sidebar.multiselect("Ownership", ["For Profit", "Non Profit", "Government"])
rating_min = st.sidebar.slider("Min Overall Star Rating", 0, 5, 0)
urban_filter = st.sidebar.radio("Urban / Rural", ["All", "Urban", "Rural"])
flags = st.sidebar.multiselect("Flags", ["Special Focus Status", "Abuse Icon", "CCRC"])

st.sidebar.divider()
st.sidebar.caption("Sources: CMS Provider Information (Apr 2026) · PBJ CY2025Q4 · LTC Characteristics · SNF Cost Report")


def apply_filters(df):
    mask = pd.Series([True] * len(df), index=df.index)
    if sel_counties and "countyparish" in df.columns:
        mask &= df["countyparish"].isin(sel_counties)
    if sel_own and "ownership_group" in df.columns:
        mask &= df["ownership_group"].isin(sel_own)
    if rating_min > 0 and "overall_rating" in df.columns:
        mask &= df["overall_rating"].fillna(0) >= rating_min
    if urban_filter == "Urban" and "urban" in df.columns:
        mask &= df["urban"] == "Y"
    elif urban_filter == "Rural" and "urban" in df.columns:
        mask &= df["urban"] == "N"
    for flag in flags:
        if flag == "Special Focus Status" and "special_focus_status" in df.columns:
            mask &= df["special_focus_status"].notna() & (df["special_focus_status"] != "")
        elif flag == "Abuse Icon" and "abuse_icon" in df.columns:
            mask &= df["abuse_icon"] == "Y"
        elif flag == "CCRC" and "continuing_care_retirement_community" in df.columns:
            mask &= df["continuing_care_retirement_community"] == "Y"
    return df[mask]


filt = apply_filters(provider)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tabs = st.tabs(["📊 Overview", "🗺️ Map", "📋 Facility Table",
                "👥 Staffing", "⭐ Quality", "🛏️ Census", "💰 Financial"])

# ═══════════════════════════════ OVERVIEW ════════════════════════════════════
with tabs[0]:
    st.title(f"{state_name} — Skilled Nursing Facilities")
    st.caption(
        f"**{len(filt):,}** of {len(provider):,} facilities shown · "
        "CMS Provider Information Apr 2026 · PBJ CY2025Q4"
    )

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Facilities", f"{len(filt):,}")
    beds_total = int(filt["number_of_certified_beds"].sum()) if "number_of_certified_beds" in filt.columns else 0
    c2.metric("Certified Beds", f"{beds_total:,}")
    census_total = int(filt["average_number_of_residents_per_day"].sum()) if "average_number_of_residents_per_day" in filt.columns else 0
    c3.metric("Avg Daily Census", f"{census_total:,}")
    occ = census_total / beds_total * 100 if beds_total else 0
    c4.metric("Occupancy Rate", f"{occ:.1f}%")
    rated = filt[filt["overall_rating"].fillna(0) > 0] if "overall_rating" in filt.columns else pd.DataFrame()
    c5.metric("Avg Star Rating", f"{rated['overall_rating'].mean():.2f} ★" if len(rated) else "—")
    hprd_col = "reported_total_nurse_staffing_hours_per_resident_per_day"
    avg_hprd = filt[filt[hprd_col].fillna(0) > 0][hprd_col].mean() if hprd_col in filt.columns else 0
    c6.metric("Avg HPRD", f"{avg_hprd:.2f}")

    col1, col2 = st.columns(2)
    with col1:
        if "overall_rating" in filt.columns:
            star_counts = filt["overall_rating"].value_counts().sort_index()
            fig = px.bar(
                x=star_counts.index.astype(str), y=star_counts.values,
                title="Overall Star Rating",
                color=star_counts.index,
                color_continuous_scale=["#ef4444","#f97316","#f59e0b","#84cc16","#22c55e"],
            )
            fig.update_layout(coloraxis_showscale=False, showlegend=False, height=300)
            st.plotly_chart(fig, use_container_width=True)
    with col2:
        if "ownership_group" in filt.columns:
            own_counts = filt["ownership_group"].value_counts()
            fig = px.pie(
                values=own_counts.values, names=own_counts.index, title="Ownership Type",
                color=own_counts.index,
                color_discrete_map={"For Profit":"#ef4444","Non Profit":"#22c55e","Government":"#3b82f6"},
            )
            fig.update_layout(height=300)
            st.plotly_chart(fig, use_container_width=True)

    col3, col4 = st.columns(2)
    with col3:
        county_col = "countyparish"
        if county_col in filt.columns:
            county_counts = filt[county_col].value_counts().head(15)
            fig = px.bar(
                x=county_counts.values, y=county_counts.index, orientation="h",
                title="Top 15 Counties / Regions", labels={"x":"Facilities","y":""},
            )
            fig.update_layout(yaxis={"categoryorder":"total ascending"}, height=380)
            st.plotly_chart(fig, use_container_width=True)
    with col4:
        if "number_of_certified_beds" in filt.columns:
            bins = pd.cut(filt["number_of_certified_beds"],
                          bins=[0,49,99,149,199,9999],
                          labels=["<50","50–99","100–149","150–199","200+"])
            bc = bins.value_counts().sort_index()
            fig = px.bar(
                x=bc.index.astype(str), y=bc.values, title="Facility Size (Beds)",
                labels={"x":"Bed Range","y":"Facilities"},
            )
            fig.update_layout(height=380)
            st.plotly_chart(fig, use_container_width=True)

    # ── Top Groups / Chains table ─────────────────────────────────────────────
    st.subheader("Top Groups by Parent Company")
    if "chain_name" in filt.columns:
        chain_filt = filt[filt["chain_name"].notna() & (filt["chain_name"] != "")].copy()

        num_cols_chain = {
            "number_of_certified_beds":                              "total_beds",
            "average_number_of_residents_per_day":                   "avg_census",
            "overall_rating":                                        "avg_rating",
            "reported_total_nurse_staffing_hours_per_resident_per_day": "avg_hprd",
            "reported_rn_staffing_hours_per_resident_per_day":       "avg_rn_hprd",
            "total_nursing_staff_turnover":                          "avg_turnover",
            "total_amount_of_fines_in_dollars":                      "total_fines",
            "total_number_of_penalties":                             "total_penalties",
        }
        for src, _ in num_cols_chain.items():
            if src in chain_filt.columns:
                chain_filt[src] = pd.to_numeric(chain_filt[src], errors="coerce")

        # Build named agg kwargs — all must be (column, aggfunc) tuples
        named_agg = {"facilities": ("chain_name", "count")}
        for src, dst, func in [
            ("number_of_certified_beds",                                  "total_beds",    "sum"),
            ("average_number_of_residents_per_day",                       "avg_census",    "sum"),
            ("overall_rating",                                            "avg_rating",    "mean"),
            ("reported_total_nurse_staffing_hours_per_resident_per_day",  "avg_hprd",      "mean"),
            ("reported_rn_staffing_hours_per_resident_per_day",           "avg_rn_hprd",   "mean"),
            ("total_nursing_staff_turnover",                              "avg_turnover",  "mean"),
            ("total_amount_of_fines_in_dollars",                          "total_fines",   "sum"),
            ("total_number_of_penalties",                                 "total_penalties","sum"),
        ]:
            if src in chain_filt.columns:
                named_agg[dst] = (src, func)

        if "special_focus_status" in chain_filt.columns:
            chain_filt["_sfs"] = (
                chain_filt["special_focus_status"].notna()
                & (chain_filt["special_focus_status"] != "")
            ).astype(int)
            named_agg["sfs_count"] = ("_sfs", "sum")

        groups = (
            chain_filt.groupby("chain_name")
            .agg(**named_agg)
            .reset_index()
            .sort_values("facilities", ascending=False)
            .head(20)
            .reset_index(drop=True)
        )
        groups.index += 1  # rank starts at 1

        # Ownership mode computed separately (lambda not allowed in named agg)
        if "ownership_group" in chain_filt.columns:
            own_mode = (
                chain_filt.groupby("chain_name")["ownership_group"]
                .agg(lambda x: x.mode().iloc[0] if len(x) else "")
                .reset_index()
                .rename(columns={"ownership_group": "ownership_mode"})
            )
            groups = groups.merge(own_mode, on="chain_name", how="left")

        # Build display dataframe — use the renamed columns from named_agg
        display = pd.DataFrame()
        display["Group / Chain"] = groups["chain_name"]
        display["Facilities"] = groups["facilities"].astype(int)
        if "total_beds" in groups.columns:
            display["Total Beds"] = groups["total_beds"].fillna(0).astype(int)
        if "avg_census" in groups.columns:
            display["Avg Daily Census"] = groups["avg_census"].fillna(0).round(0).astype(int)
        if "avg_rating" in groups.columns:
            display["Avg ★ Rating"] = groups["avg_rating"].round(2)
        if "avg_hprd" in groups.columns:
            display["Avg HPRD"] = groups["avg_hprd"].round(2)
        if "avg_rn_hprd" in groups.columns:
            display["Avg RN HPRD"] = groups["avg_rn_hprd"].round(2)
        if "avg_turnover" in groups.columns:
            display["Avg Turnover %"] = groups["avg_turnover"].round(1)
        if "total_fines" in groups.columns:
            display["Total Fines $"] = groups["total_fines"].fillna(0)
        if "sfs_count" in groups.columns:
            display["SFS Count"] = groups["sfs_count"].fillna(0).astype(int)
        if "ownership_mode" in groups.columns:
            display["Ownership"] = groups["ownership_mode"]

        ind_count = int((filt["chain_name"].isna() | (filt["chain_name"] == "")).sum())
        st.caption(
            f"Top 20 chains among **{len(chain_filt):,}** chain-affiliated facilities · "
            f"**{ind_count}** independent (no chain)"
        )

        col_cfg = {
            "Facilities":        st.column_config.NumberColumn(format="%d"),
            "Total Beds":        st.column_config.NumberColumn(format="%d"),
            "Avg Daily Census":  st.column_config.NumberColumn(format="%d"),
            "Avg ★ Rating":      st.column_config.NumberColumn(format="%.2f"),
            "Avg HPRD":          st.column_config.NumberColumn(format="%.2f"),
            "Avg RN HPRD":       st.column_config.NumberColumn(format="%.2f"),
            "Avg Turnover %":    st.column_config.NumberColumn(format="%.1f%%"),
            "Total Fines $":     st.column_config.NumberColumn(format="$%.0f"),
            "SFS Count":         st.column_config.NumberColumn(help="Facilities with Special Focus Status"),
        }
        st.dataframe(display, use_container_width=True, height=420, column_config=col_cfg)

        # Bar chart — facilities per group
        fig = px.bar(
            display.head(20),
            x="Facilities", y="Group / Chain", orientation="h",
            title="Facilities per Group (Top 20)",
            color="Avg ★ Rating" if "Avg ★ Rating" in display.columns else None,
            color_continuous_scale=["#ef4444", "#f97316", "#f59e0b", "#84cc16", "#22c55e"],
            range_color=[1, 5],
            labels={"Group / Chain": ""},
        )
        fig.update_layout(
            yaxis={"categoryorder": "total ascending"},
            height=480,
            coloraxis_colorbar=dict(title="Avg ★"),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Chain/group data not available for this state.")


# ═══════════════════════════════ MAP ════════════════════════════════════════
with tabs[1]:
    st.title(f"{state_name} — Facility Map")
    map_data = filt[
        filt["latitude"].notna() & filt["longitude"].notna()
        & filt["latitude"].between(-90,90) & filt["longitude"].between(-180,180)
    ].copy() if "latitude" in filt.columns else pd.DataFrame()

    if map_data.empty:
        st.info("No map coordinates available.")
    else:
        map_data["color_cat"] = map_data["overall_rating"].apply(
            lambda x: "4-5 Stars" if x >= 4 else "3 Stars" if x == 3
            else "1-2 Stars" if x >= 1 else "Not Rated"
        )
        map_data["_beds"] = pd.to_numeric(
            map_data["number_of_certified_beds"], errors="coerce"
        ).fillna(0).clip(lower=1)

        fig = px.scatter_mapbox(
            map_data, lat="latitude", lon="longitude",
            color="color_cat",
            color_discrete_map={
                "4-5 Stars":"#22c55e","3 Stars":"#f59e0b",
                "1-2 Stars":"#ef4444","Not Rated":"#94a3b8",
            },
            size="_beds", size_max=18,
            hover_name="provider_name",
            hover_data={
                "citytown": True, "countyparish": True,
                "number_of_certified_beds": True,
                "overall_rating": True,
                "ownership_group": True,
                "chain_name": True,
                "latitude": False, "longitude": False, "color_cat": False, "_beds": False,
            },
            mapbox_style="carto-positron",
            zoom=5, height=600,
            title=f"{len(map_data):,} {state_name} SNFs",
        )
        fig.update_layout(legend_title="Star Rating", margin=dict(l=0,r=0,t=40,b=0))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Bubble size ∝ certified beds. Use sidebar filters to narrow down.")


# ════════════════════════════ FACILITY TABLE ═════════════════════════════════
with tabs[2]:
    st.title("Facility Table")
    display_map = {
        "provider_name": "Facility",
        "citytown": "City",
        "countyparish": "County",
        "number_of_certified_beds": "Beds",
        "average_number_of_residents_per_day": "Avg Census",
        "occupancy_pct": "Occ %",
        "overall_rating": "★ Overall",
        "health_inspection_rating": "★ Health",
        "staffing_rating": "★ Staff",
        "qm_rating": "★ QM",
        "reported_total_nurse_staffing_hours_per_resident_per_day": "HPRD",
        "total_nursing_staff_turnover": "Turnover %",
        "ownership_group": "Ownership",
        "chain_name": "Chain",
        "urban": "Urban",
        "special_focus_status": "SFS",
        "abuse_icon": "Abuse",
        "total_amount_of_fines_in_dollars": "Fines $",
    }
    avail = {k: v for k, v in display_map.items() if k in filt.columns}
    tbl = filt[list(avail.keys())].copy().rename(columns=avail)
    if "Urban" in tbl.columns:
        tbl["Urban"] = tbl["Urban"].map({"Y": "Urban", "N": "Rural"})
    if "SFS" in tbl.columns:
        tbl["SFS"] = tbl["SFS"].apply(lambda x: "⚠" if pd.notna(x) and x != "" else "")
    if "Abuse" in tbl.columns:
        tbl["Abuse"] = tbl["Abuse"].map({"Y": "⚠", "N": ""}).fillna("")

    search = st.text_input("Search facility name")
    if search and "Facility" in tbl.columns:
        tbl = tbl[tbl["Facility"].str.contains(search, case=False, na=False)]

    st.caption(f"{len(tbl):,} facilities")
    col_cfg = {}
    if "Beds" in tbl.columns:       col_cfg["Beds"]       = st.column_config.NumberColumn(format="%d")
    if "Avg Census" in tbl.columns: col_cfg["Avg Census"] = st.column_config.NumberColumn(format="%.0f")
    if "Occ %" in tbl.columns:      col_cfg["Occ %"]      = st.column_config.NumberColumn(format="%.1f%%")
    if "HPRD" in tbl.columns:       col_cfg["HPRD"]       = st.column_config.NumberColumn(format="%.2f")
    if "Turnover %" in tbl.columns: col_cfg["Turnover %"] = st.column_config.NumberColumn(format="%.1f%%")
    if "Fines $" in tbl.columns:    col_cfg["Fines $"]    = st.column_config.NumberColumn(format="$%.0f")
    st.dataframe(tbl.reset_index(drop=True), use_container_width=True, height=540, column_config=col_cfg)
    st.download_button("⬇ Download CSV", tbl.to_csv(index=False).encode(), f"{selected_state}_snf_filtered.csv")


# ═══════════════════════════════ STAFFING ════════════════════════════════════
with tabs[3]:
    st.title(f"{state_name} — Staffing Analysis")
    hprd_col = "reported_total_nurse_staffing_hours_per_resident_per_day"
    rn_col   = "reported_rn_staffing_hours_per_resident_per_day"
    turn_col = "total_nursing_staff_turnover"

    h = filt[filt[hprd_col].fillna(0) > 0] if hprd_col in filt.columns else pd.DataFrame()
    t = filt[filt[turn_col].fillna(0) > 0] if turn_col in filt.columns else pd.DataFrame()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Avg Total HPRD",  f"{h[hprd_col].mean():.2f}" if len(h) else "—")
    c2.metric("Avg RN HPRD",     f"{filt[filt[rn_col].fillna(0)>0][rn_col].mean():.2f}" if rn_col in filt.columns else "—")
    c3.metric("Avg Turnover",    f"{t[turn_col].mean():.1f}%" if len(t) else "—")
    c4.metric("Below 3.5 HPRD",  int((h[hprd_col] < 3.5).sum()) if len(h) else "—")

    col1, col2 = st.columns(2)
    with col1:
        if len(h):
            fig = px.histogram(h, x=hprd_col, nbins=25, title="HPRD Distribution",
                               labels={hprd_col:"Total Nurse HPRD"},
                               color_discrete_sequence=["#1a3a5c"])
            fig.add_vline(x=3.48, line_dash="dash", line_color="red",
                          annotation_text="CMS 2024 rule (3.48)")
            fig.update_layout(height=320, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
    with col2:
        if len(t):
            fig = px.histogram(t, x=turn_col, nbins=20, title="Nursing Staff Turnover",
                               labels={turn_col:"Turnover %"},
                               color_discrete_sequence=["#3b82f6"])
            fig.update_layout(height=320, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

    col3, col4 = st.columns(2)
    with col3:
        if len(h) and "countyparish" in h.columns:
            c_hprd = (h.groupby("countyparish")[hprd_col].mean()
                      .sort_values(ascending=False).head(20).reset_index())
            fig = px.bar(c_hprd, x=hprd_col, y="countyparish", orientation="h",
                         title="Avg HPRD by County (Top 20)",
                         labels={hprd_col:"Avg HPRD","countyparish":""})
            fig.update_layout(yaxis={"categoryorder":"total ascending"}, height=420)
            st.plotly_chart(fig, use_container_width=True)
    with col4:
        if len(h) and "occupancy_pct" in h.columns:
            scatter = h[h["occupancy_pct"].between(0,120)].copy()
            fig = px.scatter(
                scatter, x="occupancy_pct", y=hprd_col,
                size="number_of_certified_beds",
                color="ownership_group",
                hover_name="provider_name",
                hover_data={"countyparish": True},
                title="HPRD vs Occupancy",
                labels={"occupancy_pct":"Occupancy %", hprd_col:"HPRD"},
                color_discrete_map={"For Profit":"#ef4444","Non Profit":"#22c55e","Government":"#3b82f6"},
                height=420,
            )
            st.plotly_chart(fig, use_container_width=True)

    # PBJ daily trends
    st.subheader("PBJ Daily Staffing Trends (CY2025 Q4)")
    with st.spinner(f"Loading PBJ data for {state_name}..."):
        pbj = load_pbj(selected_state)

    if not pbj.empty and "WorkDate" in pbj.columns:
        ccns = filt["cms_certification_number_ccn"].tolist() if "cms_certification_number_ccn" in filt.columns else []
        pbj_f = pbj[pbj["PROVNUM"].isin(ccns)] if ccns and "PROVNUM" in pbj.columns else pbj
        daily = (pbj_f.groupby("WorkDate")
                 .agg(rn=("total_rn","sum"), lpn=("total_lpn","sum"),
                      cna=("Hrs_CNA","sum"), census=("MDScensus","sum"))
                 .reset_index())
        daily[["rn","lpn","cna"]] = daily[["rn","lpn","cna"]] / 1000
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=daily["WorkDate"], y=daily["rn"],  name="RN Hours (K)",  line=dict(color="#1a3a5c")))
        fig.add_trace(go.Scatter(x=daily["WorkDate"], y=daily["lpn"], name="LPN Hours (K)", line=dict(color="#3b82f6")))
        fig.add_trace(go.Scatter(x=daily["WorkDate"], y=daily["cna"], name="CNA Hours (K)", line=dict(color="#10b981")))
        fig.update_layout(title="Daily Staffing Hours by Role", yaxis_title="Hours (thousands)",
                          height=320, legend=dict(orientation="h",y=1.1))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("PBJ daily data unavailable for this state.")


# ═══════════════════════════════ QUALITY ════════════════════════════════════
with tabs[4]:
    st.title(f"{state_name} — Quality Metrics")
    fine_col = "total_amount_of_fines_in_dollars"
    def_col  = "rating_cycle_1_total_number_of_health_deficiencies"

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("4-5 Star", int((filt["overall_rating"].fillna(0) >= 4).sum()) if "overall_rating" in filt.columns else "—")
    c2.metric("1-2 Star", int(filt["overall_rating"].between(1,2).sum()) if "overall_rating" in filt.columns else "—")
    c3.metric("Special Focus", int((filt["special_focus_status"].notna()&(filt["special_focus_status"]!="")).sum()) if "special_focus_status" in filt.columns else "—")
    c4.metric("Total Fines", f"${filt[fine_col].sum():,.0f}" if fine_col in filt.columns else "—")
    c5.metric("Avg Deficiencies", f"{filt[def_col].mean():.1f}" if def_col in filt.columns else "—")

    for col_pair in [("overall_rating","Overall"), ("health_inspection_rating","Health Inspection"),
                     ("qm_rating","Quality Measures"), ("staffing_rating","Staffing")]:
        col_name, label = col_pair
        if col_name not in filt.columns: continue

    col1, col2, col3 = st.columns(3)
    for (col_name, label), target in zip(
        [("health_inspection_rating","Health Inspection"),
         ("qm_rating","Quality Measures"),
         ("staffing_rating","Staffing")],
        [col1, col2, col3]
    ):
        if col_name not in filt.columns: continue
        with target:
            cts = filt[col_name].value_counts().sort_index()
            fig = px.bar(x=cts.index.astype(str), y=cts.values, title=f"{label} Rating",
                         color=cts.index,
                         color_continuous_scale=["#ef4444","#f97316","#f59e0b","#84cc16","#22c55e"])
            fig.update_layout(coloraxis_showscale=False, showlegend=False, height=260)
            st.plotly_chart(fig, use_container_width=True)

    col_a, col_b = st.columns(2)
    with col_a:
        if fine_col in filt.columns and "countyparish" in filt.columns:
            fc = (filt[filt[fine_col]>0].groupby("countyparish")[fine_col]
                  .sum().sort_values(ascending=False).head(15).reset_index())
            fc[fine_col] /= 1000
            fig = px.bar(fc, x=fine_col, y="countyparish", orientation="h",
                         title="Total Fines by County (Top 15, $K)",
                         labels={fine_col:"Fines ($K)","countyparish":""})
            fig.update_layout(yaxis={"categoryorder":"total ascending"}, height=380)
            st.plotly_chart(fig, use_container_width=True)
    with col_b:
        if "overall_rating" in filt.columns and "ownership_group" in filt.columns:
            r_own = (filt[filt["overall_rating"].fillna(0)>0]
                     .groupby("ownership_group")["overall_rating"].mean().reset_index())
            fig = px.bar(r_own, x="ownership_group", y="overall_rating",
                         title="Avg Star Rating by Ownership",
                         labels={"overall_rating":"Avg Rating","ownership_group":""},
                         color="ownership_group",
                         color_discrete_map={"For Profit":"#ef4444","Non Profit":"#22c55e","Government":"#3b82f6"})
            fig.update_layout(showlegend=False, yaxis_range=[0,5], height=380)
            st.plotly_chart(fig, use_container_width=True)

    if "overall_rating" in filt.columns:
        st.subheader("1-Star Facilities")
        worst_cols = [c for c in ["provider_name","citytown","countyparish",
                                   "number_of_certified_beds","health_inspection_rating",
                                   "qm_rating",def_col,fine_col,
                                   "special_focus_status","chain_name"] if c in filt.columns]
        worst = filt[filt["overall_rating"]==1].sort_values(fine_col,ascending=False) if fine_col in filt.columns else filt[filt["overall_rating"]==1]
        st.dataframe(worst[worst_cols].reset_index(drop=True), use_container_width=True, height=280)


# ═══════════════════════════════ CENSUS ════════════════════════════════════
with tabs[5]:
    st.title(f"{state_name} — Resident Census")
    with st.spinner(f"Loading LTC data for {state_name}..."):
        ltc = load_ltc(selected_state)

    ccn_col = "cms_certification_number_ccn"
    ltc_filt = ltc[ltc["Provider Number"].isin(filt[ccn_col])] if (
        not ltc.empty and "Provider Number" in ltc.columns and ccn_col in filt.columns
    ) else ltc

    if ltc_filt.empty:
        st.info("LTC characteristics data unavailable for this state.")
    else:
        tot = ltc_filt["Total Residents"].sum() if "Total Residents" in ltc_filt.columns else 0
        mc  = ltc_filt["Medicare Census"].sum()  if "Medicare Census"  in ltc_filt.columns else 0
        mcd = ltc_filt["Medicaid Census"].sum()  if "Medicaid Census"  in ltc_filt.columns else 0
        oth = ltc_filt["Other Census"].sum()     if "Other Census"     in ltc_filt.columns else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Residents", f"{int(tot):,}")
        c2.metric("Medicare",  f"{int(mc):,}  ({100*mc/tot:.0f}%)"  if tot else "—")
        c3.metric("Medicaid",  f"{int(mcd):,} ({100*mcd/tot:.0f}%)" if tot else "—")
        c4.metric("Other/Private", f"{int(oth):,} ({100*oth/tot:.0f}%)" if tot else "—")

        col1, col2 = st.columns(2)
        with col1:
            with st.spinner("Loading PBJ census..."):
                pbj = load_pbj(selected_state)
            if not pbj.empty and "WorkDate" in pbj.columns:
                ccns = filt[ccn_col].tolist() if ccn_col in filt.columns else []
                pbj_f = pbj[pbj["PROVNUM"].isin(ccns)] if ccns and "PROVNUM" in pbj.columns else pbj
                dc = pbj_f.groupby("WorkDate")["MDScensus"].sum().reset_index()
                fig = px.area(dc, x="WorkDate", y="MDScensus",
                              title="Daily Total Census (MDS, CY2025 Q4)",
                              labels={"MDScensus":"Residents","WorkDate":"Date"},
                              color_discrete_sequence=["#1a3a5c"])
                fig.update_layout(height=300)
                st.plotly_chart(fig, use_container_width=True)
        with col2:
            fig = px.pie(values=[mc, mcd, oth], names=["Medicare","Medicaid","Other/Private"],
                         title="Payer Mix",
                         color_discrete_sequence=["#3b82f6","#10b981","#f59e0b"])
            fig.update_layout(height=300)
            st.plotly_chart(fig, use_container_width=True)

        # Payer mix by ownership
        merged = ltc_filt.merge(
            filt[[ccn_col, "ownership_group"]],
            left_on="Provider Number", right_on=ccn_col, how="left"
        )
        if "ownership_group" in merged.columns:
            by_own = (merged.groupby("ownership_group")
                      [["Medicare Census","Medicaid Census","Other Census"]].sum().reset_index())
            fig = px.bar(
                by_own, x="ownership_group",
                y=["Medicare Census","Medicaid Census","Other Census"],
                title="Payer Mix by Ownership Type",
                labels={"ownership_group":"","value":"Residents","variable":"Payer"},
                color_discrete_map={"Medicare Census":"#3b82f6","Medicaid Census":"#10b981","Other Census":"#f59e0b"},
            )
            fig.update_layout(height=350, legend_title="Payer",
                              xaxis_title="", barmode="stack")
            st.plotly_chart(fig, use_container_width=True)

        # Specialized beds
        st.subheader("Specialized Beds")
        spec_rows = []
        for bed_col, label in [
            ("Number of Alzheimer's Disease Beds", "Alzheimer's Disease"),
            ("Number of Hospice Beds", "Hospice"),
            ("Number of Ventilator Beds", "Ventilator"),
            ("Number of Dialysis Beds", "Dialysis"),
        ]:
            if bed_col in ltc_filt.columns:
                spec_rows.append({
                    "Bed Type": label,
                    "Total Beds": int(ltc_filt[bed_col].sum()),
                    "Facilities": int((ltc_filt[bed_col] > 0).sum()),
                })
        if spec_rows:
            st.dataframe(pd.DataFrame(spec_rows), use_container_width=True, hide_index=True)


# ═══════════════════════════════ FINANCIAL ════════════════════════════════════
with tabs[6]:
    st.title(f"{state_name} — Financial & Utilization (SNF Cost Report)")
    with st.spinner(f"Loading cost report for {state_name}..."):
        cost = load_cost(selected_state)

    ccn_col = "cms_certification_number_ccn"
    cost_filt = cost[cost["Provider CCN"].isin(filt[ccn_col])] if (
        not cost.empty and "Provider CCN" in cost.columns and ccn_col in filt.columns
    ) else cost

    if cost_filt.empty:
        st.info("Cost report data unavailable for this state.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Facilities w/ Cost Data", len(cost_filt))
        beds_cr = int(cost_filt["Number of Beds"].sum()) if "Number of Beds" in cost_filt.columns else 0
        c2.metric("Total Beds", f"{beds_cr:,}")
        occ_cr = (cost_filt["Total Days Total"].sum() / cost_filt["Total Bed Days Available"].sum() * 100
                  if "Total Days Total" in cost_filt.columns and "Total Bed Days Available" in cost_filt.columns
                  else 0)
        c3.metric("Occupancy Rate", f"{occ_cr:.1f}%")
        los = cost_filt["SNF Average Length of Stay Title XVIII"].mean() if "SNF Average Length of Stay Title XVIII" in cost_filt.columns else 0
        c4.metric("Avg Medicare LOS", f"{los:.1f} days" if los else "—")

        col1, col2 = st.columns(2)
        with col1:
            if all(c in cost_filt.columns for c in ["Total Days Title XVIII","Total Days Title XIX","Total Days Other"]):
                payer_days = pd.DataFrame({
                    "Payer": ["Medicare","Medicaid","Other"],
                    "Patient Days": [cost_filt["Total Days Title XVIII"].sum(),
                                     cost_filt["Total Days Title XIX"].sum(),
                                     cost_filt["Total Days Other"].sum()],
                })
                fig = px.pie(payer_days, values="Patient Days", names="Payer",
                             title="Patient Days by Payer",
                             color_discrete_sequence=["#3b82f6","#10b981","#f59e0b"])
                fig.update_layout(height=300)
                st.plotly_chart(fig, use_container_width=True)
        with col2:
            if "occupancy_pct" in cost_filt.columns:
                occ_data = cost_filt[cost_filt["occupancy_pct"].between(0,120)]
                fig = px.histogram(occ_data, x="occupancy_pct", nbins=20,
                                   title="Occupancy Rate Distribution",
                                   labels={"occupancy_pct":"Occupancy %"},
                                   color_discrete_sequence=["#1a3a5c"])
                fig.add_vline(x=occ_cr, line_dash="dash", line_color="red",
                              annotation_text=f"Mean {occ_cr:.1f}%")
                fig.update_layout(height=300, showlegend=False)
                st.plotly_chart(fig, use_container_width=True)

        top_cost_cols = [c for c in ["Facility Name","City","Rural versus Urban",
                                      "Number of Beds","Total Days Total","occupancy_pct",
                                      "Total Costs","Net Patient Revenue","Net Income",
                                      "Contract Labor","SNF Admissions Total"] if c in cost_filt.columns]
        if top_cost_cols and "Total Costs" in cost_filt.columns:
            st.subheader("Top Facilities by Total Costs")
            st.dataframe(
                cost_filt.nlargest(20,"Total Costs")[top_cost_cols].reset_index(drop=True),
                use_container_width=True, height=380,
            )
