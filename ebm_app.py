import streamlit as st
import pandas as pd
from pyairtable import Api
from datetime import datetime

# --- 1. APPLICATION CONFIGURATION & VISUAL STYLING ---
st.set_page_config(
    page_title="LinkedIn Executive Analytics",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Clean, corporate metric layout optimization
st.markdown("""
<style>
    [data-testid="stMetricValue"] { font-size: 28px; font-weight: bold; }
    [data-testid="stMetricDelta"] { font-size: 14px; }
</style>
""", unsafe_allow_html=True)

# --- 2. CREDENTIAL AUTHENTICATION ---
# Securely sources credentials from Streamlit's secrets pipeline
AIRTABLE_TOKEN = st.secrets.get("AIRTABLE_TOKEN")
BASE_ID = st.secrets.get("BASE_ID")

if not AIRTABLE_TOKEN or not BASE_ID:
    st.error("❌ Configuration Missing! Please define your `AIRTABLE_TOKEN` and `BASE_ID` inside your secret management dashboard.")
    st.stop()

# Initialize API client configurations
api = Api(AIRTABLE_TOKEN)
profiles_table = api.table(BASE_ID, "Profiles")
metrics_table = api.table(BASE_ID, "Weekly Metrics")
posts_table = api.table(BASE_ID, "Posts and content")


# --- 3. DATA RECONCILIATION & PIPELINE ENGINE ---
@st.cache_data(ttl=600)  # Caches Airtable round-trips for 10 minutes
def load_all_data():
    # A. Extract payload from targets
    raw_profiles = profiles_table.all()
    raw_metrics = metrics_table.all()
    raw_posts = posts_table.all()
    
    # B. Map internal AirTable Record IDs to human-readable text strings
    id_to_name = {r['id']: r['fields'].get('Full Name', 'Unknown') for r in raw_profiles}
    
    # C. Transform and clean Weekly Metrics dataset
    metrics_data = []
    for r in raw_metrics:
        fields = r['fields'].copy()
        profile_ids = fields.get('Profile', [])
        fields['Profile Name'] = id_to_name.get(profile_ids[0], 'Unassigned') if profile_ids else 'Unassigned'
        metrics_data.append(fields)
        
    df_m = pd.DataFrame(metrics_data)
    if not df_m.empty:
        df_m['Date'] = pd.to_datetime(df_m['Date'])
        # Dynamic fallback to catch variations in how the SSI column is labeled
        ssi_col = [col for col in df_m.columns if col.startswith('SSI')][0] if [col for col in df_m.columns if col.startswith('SSI')] else 'SSI'
        df_m = df_m.rename(columns={ssi_col: 'SSI'})
    else:
        df_m = pd.DataFrame(columns=['Profile Name', 'Date', 'Total followers', 'SSI', 'Profile views', 'Appearances'])

    # D. Transform and clean Posts/Content database (matches schema column titles)
    posts_data = []
    for r in raw_posts:
        fields = r['fields'].copy()
        profile_ids = fields.get('Profile', [])
        fields['Profile Name'] = id_to_name.get(profile_ids[0], 'Unassigned') if profile_ids else 'Unassigned'
        posts_data.append(fields)
        
    df_p = pd.DataFrame(posts_data)
    if not df_p.empty and 'Publish Date' in df_p.columns:
        df_p['Publish Date'] = pd.to_datetime(df_p['Publish Date'])
    else:
        df_p = pd.DataFrame(columns=['Profile Name', 'Publish Date', 'Topic', 'Post URL', 'Impressions', 'Engagement'])
        
    return df_m, df_p


# --- 4. ENGINE EXECUTION & UN-REDACTED ERROR REPORTING ---
try:
    df_metrics, df_posts = load_all_data()
    st.sidebar.success("⚡ Live Database Sync Active")
except Exception as e:
    st.error("⚠️ Connection Mapping Breakpoint Encountered:")
    st.code(str(e))
    
    # Evaluate explicit network error footprints
    if "401" in str(e):
        st.warning("Airtable System Message: Unauthorized access. Verify your Personal Access Token string formatting.")
    elif "404" in str(e):
        st.warning("Airtable System Message: Endpoint missing. Check your Base ID or verify exact table capitalization ('Profiles', 'Weekly Metrics', 'Posts and content').")
    elif "403" in str(e):
        st.warning("Airtable System Message: Forbidden access. Ensure the target Token possesses 'data.records:read' authorization scopes inside Developer Hub.")
    
    st.stop()


# --- 5. COMPONENT FILTERS (SIDEBAR DEPLOYMENT) ---
st.sidebar.image("https://cdn-icons-png.flaticon.com/512/174/174857.png", width=40)
st.sidebar.title("Navigation Panel")

if df_metrics.empty:
    st.warning("Database setup confirmed, but no metric records were detected. Populating data log entries inside Airtable will launch the dashboard visualization engine.")
    st.stop()

# Filter selection interfaces
all_profiles = sorted(df_metrics['Profile Name'].unique())
selected_profile = st.sidebar.selectbox("🎯 Target Professional", all_profiles)

df_metrics['YearMonth'] = df_metrics['Date'].dt.to_period('M')
available_months = sorted(df_metrics['YearMonth'].unique(), reverse=True)
selected_ym = st.sidebar.selectbox("📅 Report Horizon", available_months, format_func=lambda x: x.strftime('%B %Y'))


# --- 6. AGGREGATION & MOM VARIATION MATHEMATICS ---
profile_metrics = df_metrics[df_metrics['Profile Name'] == selected_profile].sort_values('Date')

current_month_data = profile_metrics[profile_metrics['YearMonth'] == selected_ym]
prev_month_data = profile_metrics[profile_metrics['YearMonth'] == (selected_ym - 1)]

kpis = {'followers': (0, 0), 'views': (0, 0), 'appearances': (0, 0), 'ssi': (0, 0)}

if not current_month_data.empty:
    latest_row = current_month_data.iloc[-1]
    baseline_row = prev_month_data.iloc[-1] if not prev_month_data.empty else current_month_data.iloc[0]
    
    def calc_delta(field, is_absolute_diff=False):
        curr_val = latest_row.get(field, 0)
        base_val = baseline_row.get(field, 0)
        # Avoid computational failures over blank or zero entry baselines
        if pd.isna(curr_val): curr_val = 0
        if pd.isna(base_val): base_val = 0
        
        if is_absolute_diff:
            return curr_val, curr_val - base_val
        else:
            pct_change = ((curr_val - base_val) / base_val * 100) if base_val else 0
            return curr_val, pct_change

    kpis['followers'] = calc_delta('Total followers')
    kpis['views'] = calc_delta('Profile views')
    kpis['appearances'] = calc_delta('Appearances')
    kpis['ssi'] = calc_delta('SSI', is_absolute_diff=True)


# --- 7. EXECUTIVE FRONTEND DASHBOARD INTERFACE ---
st.title(f"📈 Performance Analysis: {selected_profile}")
st.markdown(f"Monthly evaluation brief tracking performance indexes across **{selected_ym.strftime('%B %Y')}** relative to matching historical timelines.")
st.markdown("---")

# Layout Quadrants: Core High-Level KPIs
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        label="Total Audience Reach", 
        value=f"{int(kpis['followers'][0]):,}", 
        delta=f"{kpis['followers'][1]:+.1f}% MoM" if kpis['followers'][1] else "0.0% MoM"
    )

with col2:
    st.metric(
        label="Profile Discovery Views", 
        value=f"{int(kpis['views'][0]):,}", 
        delta=f"{kpis['views'][1]:+.1f}% MoM" if kpis['views'][1] else "0.0% MoM"
    )

with col3:
    st.metric(
        label="Search Appearances", 
        value=f"{int(kpis['appearances'][0]):,}", 
        delta=f"{kpis['appearances'][1]:+.1f}% MoM" if kpis['appearances'][1] else "0.0% MoM"
    )

with col4:
    st.metric(
        label="Social Selling Index", 
        value=f"{int(kpis['ssi'][0])}/100", 
        delta=f"{kpis['ssi'][1]:+g} pts MoM" if kpis['ssi'][1] else "0 pts"
    )

st.markdown("---")

# Layout Row: Time Series Trend Visualizations vs Raw Structural Data Maps
chart_col, table_col = st.columns([2, 1])

with chart_col:
    st.subheader("📈 Long-Term Growth Vector (All-Time)")
    if not profile_metrics.empty:
        chart_df = profile_metrics.set_index('Date')[['Total followers']]
        st.line_chart(chart_df, color="#0a66c2")
    else:
        st.info("Historical tracking points are insufficient to project directional trajectory curves.")

with table_col:
    st.subheader("📋 Core Data Logs This Period")
    if not current_month_data.empty:
        display_raw = current_month_data[['Date', 'Total followers', 'Profile views', 'Appearances', 'SSI']].copy()
        display_raw['Date'] = display_raw['Date'].dt.strftime('%Y-%m-%d')
        st.dataframe(display_raw.set_index('Date'), use_container_width=True)
    else:
        st.info("No recorded time entries map against the designated tracking horizon.")

st.markdown("---")

# Layout Segment: Content Optimization & Engagement Analysis
st.subheader("🏆 Leading Content Performance Highlight")

profile_posts = df_posts[
    (df_posts['Profile Name'] == selected_profile) & 
    (df_posts['Publish Date'].dt.to_period('M') == selected_ym)
] if not df_posts.empty else pd.DataFrame()

if not profile_posts.empty and 'Impressions' in profile_posts.columns:
    # Identify record containing supreme monthly impressions volume
    top_post = profile_posts.sort_values(by='Impressions', ascending=False).iloc[0]
    
    p_col1, p_col2 = st.columns([1, 2])
    with p_col1:
        st.info(f"**💡 Editorial Focus / Hook Summary:**\n\n*{top_post.get('Topic', 'No Overview Value Logged')}*")
        if pd.notna(top_post.get('Post URL')):
            st.link_button("🔗 Launch Live Content Link", top_post['Post URL'])
            
    with p_col2:
        metric_p1, metric_p2 = st.columns(2)
        metric_p1.metric("Organic Impressions", f"{int(top_post.get('Impressions', 0)):,}")
        metric_p2.metric("Total Engagement Interactions", f"{int(top_post.get('Engagement', 0)):,}")
else:
    st.info("No content marketing metrics or interactions were logged for this creator during this month.")
