import streamlit as st
import pandas as pd
from pyairtable import Api
from datetime import datetime

# --- 1. APPS CONFIGURATION & STYLING ---
st.set_page_config(
    page_title="LinkedIn Executive Analytics",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom metric styling for a cleaner corporate look
st.markdown("""
<style>
    [data-testid="stMetricValue"] { font-size: 28px; font-weight: bold; }
    [data-testid="stMetricDelta"] { font-size: 14px; }
</style>
""", unsafe_allow_html=True)

# --- 2. SECURE API CONNECTION ---
# Fetches keys securely from .streamlit/secrets.toml
AIRTABLE_TOKEN = st.secrets.get("AIRTABLE_TOKEN")
BASE_ID = st.secrets.get("BASE_ID")

if not AIRTABLE_TOKEN or not BASE_ID:
    st.error("❌ Credentials missing! Please check that your `.streamlit/secrets.toml` file is set up correctly.")
    st.stop()

api = Api(AIRTABLE_TOKEN)
profiles_table = api.table(BASE_ID, "Profiles")
metrics_table = api.table(BASE_ID, "Weekly Metrics")
posts_table = api.table(BASE_ID, "Posts and content")


# --- 3. DATA PIPELINE (FETCH, CLEAN, & MAP) ---
@st.cache_data(ttl=600)  # Caches data for 10 minutes to keep loading instant
def load_all_data():
    # A. Fetch raw records
    raw_profiles = profiles_table.all()
    raw_metrics = metrics_table.all()
    raw_posts = posts_table.all()
    
    # B. Map Airtable's internal record IDs to actual names (e.g., {"recXYZ": "Jane Doe"})
    id_to_name = {r['id']: r['fields'].get('Full Name', 'Unknown') for r in raw_profiles}
    
    # C. Clean & Process Weekly Metrics
    metrics_data = []
    for r in raw_metrics:
        fields = r['fields'].copy()
        profile_ids = fields.get('Profile', [])
        # Resolve linked profile record ID to plain text name
        fields['Profile Name'] = id_to_name.get(profile_ids[0], 'Unassigned') if profile_ids else 'Unassigned'
        metrics_data.append(fields)
        
    df_m = pd.DataFrame(metrics_data)
    if not df_m.empty:
        df_m['Date'] = pd.to_datetime(df_m['Date'])
        # Dynamic fallback column for SSI variations
        ssi_col = [col for col in df_m.columns if col.startswith('SSI')][0] if [col for col in df_m.columns if col.startswith('SSI')] else 'SSI'
        df_m = df_m.rename(columns={ssi_col: 'SSI'})
    else:
        # Create empty placeholder dataframe with correct columns if no data exists yet
        df_m = pd.DataFrame(columns=['Profile Name', 'Date', 'Total followers', 'SSI', 'Profile views', 'Appearances'])

    # D. Clean & Process Posts and Content
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


# Execute Pipeline
df_metrics, df_posts = load_all_data()

# --- 4. SIDEBAR FILTERS ---
st.sidebar.image("https://cdn-icons-png.flaticon.com/512/174/174857.png", width=50)
st.sidebar.title("Navigation Hub")

if df_metrics.empty:
    st.warning("No data found in Airtable yet. Add some weekly rows to see the dashboard live!")
    st.stop()

# Filter: Profile Selection
all_profiles = sorted(df_metrics['Profile Name'].unique())
selected_profile = st.sidebar.selectbox("🎯 Select Profile", all_profiles)

# Create Year-Month strings for filtering (e.g., "2026-07")
df_metrics['YearMonth'] = df_metrics['Date'].dt.to_period('M')
available_months = sorted(df_metrics['YearMonth'].unique(), reverse=True)
selected_ym = st.sidebar.selectbox("📅 Reporting Month", available_months, format_func=lambda x: x.strftime('%B %Y'))

# --- 5. DATA AGGREGATION & MONTH-OVER-MONTH LOGIC ---
# Filter data for selected user
profile_metrics = df_metrics[df_metrics['Profile Name'] == selected_profile].sort_values('Date')

# Get current month metrics and previous month metrics for true delta comparison
current_month_data = profile_metrics[profile_metrics['YearMonth'] == selected_ym]
prev_month_data = profile_metrics[profile_metrics['YearMonth'] == (selected_ym - 1)]

# Initialize default empty KPI blocks
kpis = {'followers': (0, 0), 'views': (0, 0), 'appearances': (0, 0), 'ssi': (0, 0)}

if not current_month_data.empty:
    # Latest data point available in the selected month
    latest_row = current_month_data.iloc[-1]
    
    # If previous month data exists, baseline against its final entry. 
    # Otherwise baseline against the earliest available record in the current month.
    baseline_row = prev_month_data.iloc[-1] if not prev_month_data.empty else current_month_data.iloc[0]
    
    def calc_delta(field, is_absolute_diff=False):
        curr_val = latest_row.get(field, 0)
        base_val = baseline_row.get(field, 0)
        if is_absolute_diff:
            return curr_val, curr_val - base_val
        else:
            pct_change = ((curr_val - base_val) / base_val * 100) if base_val else 0
            return curr_val, pct_change

    kpis['followers'] = calc_delta('Total followers')
    kpis['views'] = calc_delta('Profile views')
    kpis['appearances'] = calc_delta('Appearances')
    kpis['ssi'] = calc_delta('SSI', is_absolute_diff=True)

# --- 6. DASHBOARD INTERFACE LAYOUT ---
st.title(f"📈 Executive Performance: {selected_profile}")
st.markdown(f"Monthly reporting metrics for **{selected_ym.strftime('%B %Y')}** compared against preceding baselines.")
st.markdown("---")

# Row 1: KPI Summary Cards
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        label="Total Followers", 
        value=f"{int(kpis['followers'][0]):,}", 
        delta=f"{kpis['followers'][1]:+.1f}% MoM" if kpis['followers'][1] else "0% MoM"
    )

with col2:
    st.metric(
        label="Profile Views", 
        value=f"{int(kpis['views'][0]):,}", 
        delta=f"{kpis['views'][1]:+.1f}% MoM" if kpis['views'][1] else "0% MoM"
    )

with col3:
    st.metric(
        label="Search Appearances", 
        value=f"{int(kpis['appearances'][0]):,}", 
        delta=f"{kpis['appearances'][1]:+.1f}% MoM" if kpis['appearances'][1] else "0% MoM"
    )

with col4:
    st.metric(
        label="SSI Score", 
        value=f"{int(kpis['ssi'][0])}/100", 
        delta=f"{kpis['ssi'][1]:+g} pts MoM" if kpis['ssi'][1] else "0 pts"
    )

st.markdown("---")

# Row 2: Charts & Visual Trends over time
chart_col, table_col = st.columns([2, 1])

with chart_col:
    st.subheader("📈 Follower Growth Trend (All-Time)")
    if not profile_metrics.empty:
        # Build clean time-series chart
        chart_df = profile_metrics.set_index('Date')[['Total followers']]
        st.line_chart(chart_df, color="#1a73e8")
    else:
        st.info("Insufficient timeline data to map historical trends.")

with table_col:
    st.subheader("📋 Raw Logs This Month")
    if not current_month_data.empty:
        display_raw = current_month_data[['Date', 'Total followers', 'Profile views', 'Appearances', 'SSI']].copy()
        display_raw['Date'] = display_raw['Date'].dt.strftime('%Y-%m-%d')
        st.dataframe(display_raw.set_index('Date'), use_container_width=True)
    else:
        st.info("No raw data logs for this period.")

st.markdown("---")

# Row 3: Post Content Analytics
st.subheader("🏆 Top Performing Content This Month")

profile_posts = df_posts[
    (df_posts['Profile Name'] == selected_profile) & 
    (df_posts['Publish Date'].dt.to_period('M') == selected_ym)
] if not df_posts.empty else pd.DataFrame()

if not profile_posts.empty:
    # Find the row containing top impressions
    top_post = profile_posts.sort_values(by='Impressions', ascending=False).iloc[0]
    
    p_col1, p_col2 = st.columns([1, 2])
    with p_col1:
        st.info(f"**💡 Content Topic / Hook:**\n\n*{top_post.get('Topic', 'No Title Specified')}*")
        if pd.notna(top_post.get('Post URL')):
            st.link_button("🔗 View Original LinkedIn Post", top_post['Post URL'])
            
    with p_col2:
        metric_p1, metric_p2 = st.columns(2)
        metric_p1.metric("Post Impressions", f"{int(top_post.get('Impressions', 0)):,}")
        metric_p2.metric("Post Engagements", f"{int(top_post.get('Engagement', 0)):,}")
else:
    st.info("No content posts or interaction metrics were recorded for this professional during this specific month.")
