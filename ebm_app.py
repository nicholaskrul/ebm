import streamlit as st
import pandas as pd
from pyairtable import Api
from datetime import datetime
import io
from weasyprint import HTML

# --- 1. APPLICATION CONFIGURATION & VISUAL STYLING ---
st.set_page_config(
    page_title="LinkedIn Executive Analytics",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    [data-testid="stMetricValue"] { font-size: 28px; font-weight: bold; }
    [data-testid="stMetricDelta"] { font-size: 14px; }
</style>
""", unsafe_allow_html=True)

# --- 2. CREDENTIAL AUTHENTICATION ---
AIRTABLE_TOKEN = st.secrets.get("AIRTABLE_TOKEN")
BASE_ID = st.secrets.get("BASE_ID")

if not AIRTABLE_TOKEN or not BASE_ID:
    st.error("❌ Configuration Missing! Please define your `AIRTABLE_TOKEN` and `BASE_ID` inside your secret management dashboard.")
    st.stop()

api = Api(AIRTABLE_TOKEN)
profiles_table = api.table(BASE_ID, "Profiles")
metrics_table = api.table(BASE_ID, "Weekly Metrics")
posts_table = api.table(BASE_ID, "Posts and content")


# --- 3. DATA RECONCILIATION & PIPELINE ENGINE ---
@st.cache_data(ttl=600)
def load_all_data():
    raw_profiles = profiles_table.all()
    raw_metrics = metrics_table.all()
    raw_posts = posts_table.all()
    
    id_to_name = {r['id']: r['fields'].get('Full Name', 'Unknown') for r in raw_profiles}
    id_to_title = {r['id']: r['fields'].get('Job Title', 'Executive') for r in raw_profiles}
    
    metrics_data = []
    for r in raw_metrics:
        fields = r['fields'].copy()
        profile_ids = fields.get('Profile', [])
        fields['Profile Name'] = id_to_name.get(profile_ids[0], 'Unassigned') if profile_ids else 'Unassigned'
        fields['Job Title'] = id_to_title.get(profile_ids[0], 'Executive') if profile_ids else 'Executive'
        metrics_data.append(fields)
        
    df_m = pd.DataFrame(metrics_data)
    if not df_m.empty:
        df_m['Date'] = pd.to_datetime(df_m['Date'])
        ssi_col = [col for col in df_m.columns if col.startswith('SSI')][0] if [col for col in df_m.columns if col.startswith('SSI')] else 'SSI'
        df_m = df_m.rename(columns={ssi_col: 'SSI'})
    else:
        df_m = pd.DataFrame(columns=['Profile Name', 'Job Title', 'Date', 'Total followers', 'SSI', 'Profile views', 'Appearances'])

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


try:
    df_metrics, df_posts = load_all_data()
    st.sidebar.success("⚡ Live Database Sync Active")
except Exception as e:
    st.error("⚠️ Connection Mapping Breakpoint Encountered:")
    st.code(str(e))
    st.stop()


# --- 4. COMPONENT FILTERS ---
st.sidebar.image("https://cdn-icons-png.flaticon.com/512/174/174857.png", width=40)
st.sidebar.title("Navigation Panel")

if df_metrics.empty:
    st.warning("Database setup confirmed, but no metric records were detected.")
    st.stop()

all_profiles = sorted(df_metrics['Profile Name'].unique())
selected_profile = st.sidebar.selectbox("🎯 Target Professional", all_profiles)

df_metrics['YearMonth'] = df_metrics['Date'].dt.to_period('M')
available_months = sorted(df_metrics['YearMonth'].unique(), reverse=True)
selected_ym = st.sidebar.selectbox("📅 Report Horizon", available_months, format_func=lambda x: x.strftime('%B %Y'))


# --- 5. AGGREGATION & MOM VARIATION MATHEMATICS ---
profile_metrics = df_metrics[df_metrics['Profile Name'] == selected_profile].sort_values('Date')
current_month_data = profile_metrics[profile_metrics['YearMonth'] == selected_ym]
prev_month_data = profile_metrics[profile_metrics['YearMonth'] == (selected_ym - 1)]

kpis = {'followers': (0, 0), 'views': (0, 0), 'appearances': (0, 0), 'ssi': (0, 0)}
inception_data = {'followers_growth': 0, 'ssi_growth': 0}
job_title = "Executive"

if not profile_metrics.empty:
    job_title = profile_metrics.iloc[-1].get('Job Title', 'Executive')
    earliest_row = profile_metrics.iloc[0]

if not current_month_data.empty:
    latest_row = current_month_data.iloc[-1]
    baseline_row = prev_month_data.iloc[-1] if not prev_month_data.empty else current_month_data.iloc[0]
    
    def calc_delta(field, is_absolute_diff=False):
        curr_val = latest_row.get(field, 0)
        base_val = baseline_row.get(field, 0)
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
    
    # Inception Growth Calculations (Current metrics minus the very first log entry in history)
    inception_data['followers_growth'] = int(latest_row.get('Total followers', 0) - earliest_row.get('Total followers', 0))
    inception_data['ssi_growth'] = int(latest_row.get('SSI', 0) - earliest_row.get('SSI', 0))


# --- 6. EXPORT PDF GENERATION ENGINE (WEASYPRINT) ---
def generate_pdf_report():
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            @page {
                size: A4;
                margin: 20mm 15mm;
                background-color: #f8fafc;
            }
            * {
                box-sizing: border-box;
            }
            body {
                font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
                color: #1e293b;
                margin: 0;
                padding: 0;
                font-size: 10pt;
                line-height: 1.5;
            }
            .header-container {
                background-color: #1e3a8a;
                color: #ffffff;
                padding: 24px;
                border-radius: 8px;
                margin-bottom: 25px;
            }
            .header-table {
                display: table;
                width: 100%;
            }
            .header-row {
                display: table-row;
            }
            .header-cell {
                display: table-cell;
                vertical-align: middle;
            }
            .header-right {
                text-align: right;
                font-size: 11pt;
                color: #93c5fd;
            }
            h1 {
                font-size: 20pt;
                margin: 0 0 4px 0;
                font-weight: 700;
                letter-spacing: -0.5px;
            }
            .subtitle {
                font-size: 12pt;
                margin: 0;
                color: #bfdbfe;
            }
            h2 {
                font-size: 13pt;
                color: #0f172a;
                border-bottom: 2px solid #e2e8f0;
                padding-bottom: 6px;
                margin-top: 30px;
                margin-bottom: 15px;
                page-break-after: avoid;
            }
            .section-desc {
                font-size: 10pt;
                color: #64748b;
                margin-top: -10px;
                margin-bottom: 15px;
            }
            .grid-table {
                display: table;
                width: 100%;
                border-collapse: separate;
                border-spacing: 12px 0;
                margin: 0 -12px 20px -12px;
                page-break-inside: avoid;
            }
            .grid-row {
                display: table-row;
            }
            .grid-card {
                display: table-cell;
                width: 50%;
                background: #ffffff;
                padding: 18px;
                border-radius: 6px;
                border: 1px solid #e2e8f0;
                border-top: 4px solid #2563eb;
                vertical-align: top;
            }
            .grid-card.alt {
                border-top: 4px solid #0d9488;
            }
            .metric-title {
                font-size: 10pt;
                text-transform: uppercase;
                color: #64748b;
                font-weight: 600;
                margin-bottom: 8px;
            }
            .metric-value {
                font-size: 22pt;
                font-weight: 700;
                color: #0f172a;
                margin-bottom: 12px;
            }
            .sub-metrics {
                display: table;
                width: 100%;
                border-top: 1px solid #f1f5f9;
                padding-top: 10px;
            }
            .sub-metric-row {
                display: table-row;
            }
            .sub-metric-label {
                display: table-cell;
                font-size: 9.5pt;
                color: #475569;
                padding: 4px 0;
            }
            .sub-metric-val {
                display: table-cell;
                font-size: 10pt;
                font-weight: 600;
                text-align: right;
                color: #0f172a;
                padding: 4px 0;
            }
            .positive { color: #16a34a; }
            .negative { color: #dc2626; }
            
            .plain-table {
                display: table;
                width: 100%;
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                margin-bottom: 20px;
                page-break-inside: avoid;
            }
            .plain-row {
                display: table-row;
            }
            .plain-cell {
                display: table-cell;
                padding: 14px 18px;
                border-bottom: 1px solid #e2e8f0;
            }
            .plain-row:last-child .plain-cell {
                border-bottom: none;
            }
            .plain-label {
                font-weight: 600;
                color: #334155;
                width: 60%;
            }
            .plain-val {
                text-align: right;
                font-weight: 700;
                font-size: 13pt;
                color: #0f172a;
            }
            .footer {
                text-align: center;
                font-size: 8.5pt;
                color: #94a3b8;
                margin-top: 40px;
                border-top: 1px solid #e2e8f0;
                padding-top: 15px;
            }
        </style>
    </head>
    <body>

        <div class="header-container">
            <div class="header-table">
                <div class="header-row">
                    <div class="header-cell">
                        <h1>__PROFILE_NAME__</h1>
                        <div class="subtitle">__TITLE__</div>
                    </div>
                    <div class="header-cell header-right">
                        <strong>Executive Performance Brief</strong><br>
                        __MONTH_STR__
                    </div>
                </div>
            </div>
        </div>

        <h2>Network Audience Metrics</h2>
        <div class="section-desc">Analysis of total market reach and growth variations over the active performance horizon.</div>
        
        <div class="grid-table">
            <div class="grid-row">
                <div class="grid-card">
                    <div class="metric-title">Total Followers</div>
                    <div class="metric-value">__FOLLOWERS_VAL__</div>
                    
                    <div class="sub-metrics">
                        <div class="sub-metric-row">
                            <div class="sub-metric-label">Month-on-Month Growth</div>
                            <div class="sub-metric-val __FOL_CLASS__">
                                __FOL_MOM__
                            </div>
                        </div>
                        <div class="sub-metric-row">
                            <div class="sub-metric-label">Net Growth Since Inception</div>
                            <div class="sub-metric-val positive">__FOL_INC__</div>
                        </div>
                    </div>
                </div>

                <div class="grid-card alt">
                    <div class="metric-title">Social Selling Index (SSI)</div>
                    <div class="metric-value">__SSI_VAL__ <span style="font-size: 11pt; color: #64748b; font-weight: normal;">/ 100</span></div>
                    
                    <div class="sub-metrics">
                        <div class="sub-metric-row">
                            <div class="sub-metric-label">Month-on-Month Shift</div>
                            <div class="sub-metric-val __SSI_CLASS__">
                                __SSI_MOM__
                            </div>
                        </div>
                        <div class="sub-metric-row">
                            <div class="sub-metric-label">Net Shift Since Inception</div>
                            <div class="sub-metric-val __SSI_INC_CLASS__">
                                __SSI_INC__
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <h2>Profile Visibility & Interactions</h2>
        <div class="section-desc">Key metadata signaling profile organically discovered traffic volumes and search engine placement indexes.</div>

        <div class="plain-table">
            <div class="plain-row">
                <div class="plain-cell plain-label">Profile Discovery Views (Last 90 Days)</div>
                <div class="plain-cell plain-val">__VIEWS_VAL__</div>
            </div>
            <div class="plain-row">
                <div class="plain-cell plain-label">Search Appearances Queries</div>
                <div class="plain-cell plain-val">__APP_VAL__</div>
            </div>
        </div>

        <div class="footer">
            Generated via LinkedIn Executive Hub Integration Engine • Confidential Executive Document
        </div>

    </body>
    </html>
    """
    
    fol_mom_val = kpis['followers'][1]
    ssi_mom_val = kpis['ssi'][1]
    ssi_inc_val = inception_data['ssi_growth']
    
    # Clean token replacing maps to completely bypass css bracket clashing
    formatted_html = html_template.replace("__PROFILE_NAME__", selected_profile)\
                                  .replace("__TITLE__", job_title)\
                                  .replace("__MONTH_STR__", selected_ym.strftime('%B %Y'))\
                                  .replace("__FOLLOWERS_VAL__", f"{int(kpis['followers'][0]):,}")\
                                  .replace("__FOL_MOM__", f"{fol_mom_val:+.1f}%")\
                                  .replace("__FOL_CLASS__", "positive" if fol_mom_val >= 0 else "negative")\
                                  .replace("__FOL_INC__", f"+{inception_data['followers_growth']:,}")\
                                  .replace("__SSI_VAL__", f"{int(kpis['ssi'][0])}")\
                                  .replace("__SSI_MOM__", f"{ssi_mom_val:+g} pts")\
                                  .replace("__SSI_CLASS__", "positive" if ssi_mom_val >= 0 else "negative")\
                                  .replace("__SSI_INC__", f"{ssi_inc_val:+g} pts")\
                                  .replace("__SSI_INC_CLASS__", "positive" if ssi_inc_val >= 0 else "negative")\
                                  .replace("__VIEWS_VAL__", f"{int(kpis['views'][0]):,}")\
                                  .replace("__APP_VAL__", f"{int(kpis['appearances'][0]):,}")
    
    pdf_buffer = io.BytesIO()
    HTML(string=formatted_html).write_pdf(pdf_buffer)
    return pdf_buffer.getvalue()


# --- 7. EXECUTIVE FRONTEND DASHBOARD INTERFACE ---
st.title(f"📈 Performance Analysis: {selected_profile}")
st.markdown(f"Monthly evaluation brief tracking performance indexes across **{selected_ym.strftime('%B %Y')}**.")

# Add PDF Generation action to the sidebar control hub
st.sidebar.markdown("---")
st.sidebar.subheader("🗂️ Report Generation")
with st.sidebar:
    if not current_month_data.empty:
        try:
            pdf_data = generate_pdf_report()
            st.download_button(
                label="📥 Export Monthly PDF Report",
                data=pdf_data,
                file_name=f"LinkedIn_Report_{selected_profile}_{selected_ym.strftime('%Y_%m')}.pdf",
                mime="application/pdf",
                use_container_width=True
            )
        except Exception as e:
            st.error(f"PDF Generator Error: {e}")
    else:
        st.info("Select a month with log entries to enable PDF exporting.")

st.markdown("---")

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
