import streamlit as st
import pandas as pd
from pyairtable import Api
from datetime import datetime
import io
import requests
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
from weasyprint import HTML

# --- 1. APPLICATION CONFIGURATION & VISUAL STYLING ---
st.set_page_config(
    page_title="LinkedIn Executive Analytics",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown('''
<style>
    [data-testid="stMetricValue"] { font-size: 26px; font-weight: bold; }
    [data-testid="stMetricDelta"] { font-size: 13px; }
    .comment-box { border-left: 4px solid #0a66c2; padding-left: 15px; margin: 10px 0; background-color: #f4f6f9; padding: 10px; border-radius: 4px; }
</style>
''', unsafe_allow_html=True)

# --- 2. STATE MANAGEMENT FOR INTERACTIVE COMMENTS ---
if "manager_notes" not in st.session_state:
    st.session_state.manager_notes = {}

# --- 3. CREDENTIAL AUTHENTICATION & RATE LIMIT PROTECTION ---
AIRTABLE_TOKEN = st.secrets.get("AIRTABLE_TOKEN")
BASE_ID = st.secrets.get("BASE_ID")

if not AIRTABLE_TOKEN or not BASE_ID:
    st.error("❌ Configuration Missing! Define your `AIRTABLE_TOKEN` and `BASE_ID` inside your secret management dashboard.")
    st.stop()

# Initialize the standard API client first
api = Api(AIRTABLE_TOKEN)

# Build an enterprise retry framework to completely bypass Airtable 5req/sec limits
retries = Retry(
    total=5,                                    
    backoff_factor=1,                           
    status_forcelist=[429, 500, 502, 503, 504], 
    raise_on_status=True
)

# Inject the rate-limit protector directly into pyairtable's built-in session object
api.session.mount("https://", HTTPAdapter(max_retries=retries))

# Define your tables using the protected API instance
profiles_table = api.table(BASE_ID, "Profiles")
metrics_table = api.table(BASE_ID, "Weekly Metrics")
posts_table = api.table(BASE_ID, "Posts and content")


# --- 4. DATA RECONCILIATION & PIPELINE ENGINE ---
@st.cache_data(ttl=600)  
def load_all_data():
    raw_profiles = profiles_table.all()
    raw_metrics = metrics_table.all()
    raw_posts = posts_table.all()
    
    id_to_name = {r['id']: r['fields'].get('Full Name', 'Unknown') for r in raw_profiles}
    id_to_title = {r['id']: r['fields'].get('Job Title', 'Executive') for r in raw_profiles}
    
    # Process Metrics Dataset
    metrics_data = []
    for r in raw_metrics:
        fields = r['fields'].copy()
        profile_ids = fields.get('Profile', [])
        fields['Profile Name'] = id_to_name.get(profile_ids[0], 'Unassigned') if profile_ids else 'Unassigned'
        fields['Job Title'] = id_to_title.get(profile_ids[0], 'Executive') if profile_ids else 'Executive'
        metrics_data.append(fields)
        
    df_m = pd.DataFrame(metrics_data)
    if not df_m.empty:
        df_m = df_m.dropna(subset=['Date'])
        df_m['Date'] = pd.to_datetime(df_m['Date'])
        ssi_col = [col for col in df_m.columns if col.startswith('SSI')][0] if [col for col in df_m.columns if col.startswith('SSI')] else 'SSI'
        df_m = df_m.rename(columns={ssi_col: 'SSI'})
    else:
        df_m = pd.DataFrame(columns=['Profile Name', 'Job Title', 'Date', 'Total followers', 'SSI', 'Profile views', 'Appearances'])
        
    # Process Content Logs Dataset
    posts_data = []
    for r in raw_posts:
        fields = r['fields'].copy()
        profile_ids = fields.get('Profile', [])
        fields['Profile Name'] = id_to_name.get(profile_ids[0], 'Unassigned') if profile_ids else 'Unassigned'
        posts_data.append(fields)
        
    df_p = pd.DataFrame(posts_data)
    if not df_p.empty and 'Publish Date' in df_p.columns:
        df_p = df_p.dropna(subset=['Publish Date'])
        df_p['Publish Date'] = pd.to_datetime(df_p['Publish Date'])
        df_p['YearMonth'] = df_p['Publish Date'].dt.to_period('M')
        for metric_col in ['Impressions', 'Engagement']:
            if metric_col in df_p.columns:
                df_p[metric_col] = pd.to_numeric(df_p[metric_col]).fillna(0)
            else:
                df_p[metric_col] = 0
    else:
        df_p = pd.DataFrame(columns=['Profile Name', 'Publish Date', 'YearMonth', 'Impressions', 'Engagement'])
        
    return df_m, df_p


try:
    df_metrics, df_posts = load_all_data()
    st.sidebar.success("⚡ Live Database Sync Active")
except Exception as e:
    st.error(f"⚠️ Connection Mapping Breakpoint Encountered: {e}")
    st.stop()

# Safely establish timelines while avoiding blanks
df_metrics['YearMonth'] = df_metrics['Date'].dt.to_period('M')
available_months = sorted(df_metrics['YearMonth'].dropna().unique(), reverse=True)
all_profiles_list = sorted(df_metrics['Profile Name'].unique())

# --- GLOBAL NAVIGATION CONTROL PANEL ---
st.sidebar.title("Navigation Panel")
if not available_months:
    st.sidebar.error("❌ No valid time tracking records were parsed out from Airtable data columns.")
    st.stop()

selected_ym = st.sidebar.selectbox("📅 Reporting Horizon", available_months, format_func=lambda x: x.strftime('%B %Y'))
# FIX: Moved from tab body to global sidebar to permanently eliminate tab-ghosting layout bugs
selected_profile = st.sidebar.selectbox("🎯 Target Professional Focus", all_profiles_list)


# --- 5. COMPREHENSIVE TEAM METRICS METRIC CALCULATOR ---
team_records = []
for name in all_profiles_list:
    prof_df = df_metrics[df_metrics['Profile Name'] == name].sort_values('Date')
    if prof_df.empty: continue
    
    c_month = prof_df[prof_df['YearMonth'] == selected_ym]
    p_month = prof_df[prof_df['YearMonth'] == (selected_ym - 1)]
    
    earliest = prof_df.iloc[0]
    latest = c_month.iloc[-1] if not c_month.empty else prof_df.iloc[-1]
    baseline = p_month.iloc[-1] if not p_month.empty else (c_month.iloc[0] if not c_month.empty else earliest)
    
    f_curr, f_base, f_early = latest.get('Total followers', 0), baseline.get('Total followers', 0), earliest.get('Total followers', 0)
    s_curr, s_base, s_early = latest.get('SSI', 0), baseline.get('SSI', 0), earliest.get('SSI', 0)
    v_curr = latest.get('Profile views', 0)
    a_curr = latest.get('Appearances', 0)
    
    fol_mom = ((f_curr - f_base) / f_base * 100) if f_base else 0
    fol_inc = f_curr - f_early
    ssi_mom = s_curr - s_base
    ssi_inc = s_curr - s_early
    
    month_posts = df_posts[(df_posts['Profile Name'] == name) & (df_posts['YearMonth'] == selected_ym)]
    posts_count = len(month_posts)
    
    team_records.append({
        'Profile Name': name,
        'Job Title': latest.get('Job Title', 'Executive'),
        'Followers': f_curr,
        'Followers MoM%': fol_mom,
        'Followers Inc Growth': fol_inc,
        'SSI': s_curr,
        'SSI MoM Shift': ssi_mom,
        'SSI Inc Shift': ssi_inc,
        'Views': v_curr,
        'Appearances': a_curr,
        'Posts Published': posts_count,
        'Date': latest['Date']
    })

df_team_standings = pd.DataFrame(team_records)

# Stabilize inputs across cache clearances
for name in all_profiles_list:
    if name not in st.session_state.manager_notes:
        st.session_state.manager_notes[name] = ""


# --- 6. TOP-LEVEL DASHBOARD SYSTEM SEGMENTATION ---
tab_team, tab_individual = st.tabs(["👥 Combined Team Overview", "🎯 Individual Profile Deep Dive"])


# ==========================================
# 👥 TAB 1: MASTER COMBINED TEAM METRIC HUB
# ==========================================
with tab_team:
    st.subheader("👥 Managed Portfolio Summary Leaderboard")
    st.markdown("Aggregated standings with cross-profile analytics tracking overall, monthly progress, and content velocity.")
    st.markdown("---")
    
    with st.expander("📝 Edit Executive Monthly Commentary Notes"):
        cmt_cols = st.columns(2)
        for idx, name in enumerate(all_profiles_list):
            target_col = cmt_cols[0] if idx % 2 == 0 else cmt_cols[1]
            with target_col:
                st.session_state.manager_notes[name] = st.text_area(
                    f"Notes for {name} ({selected_ym.strftime('%b %Y')}):",
                    value=st.session_state.manager_notes[name],
                    key=f"team_notes_{name}"
                )
                
    def generate_team_progress_pdf(df_source):
        html_template = """
        <!DOCTYPE html><html><head><meta charset='utf-8'><style>
            @page { size: A4 landscape; margin: 12mm; background-color: #fafbfc; }
            body { font-family: sans-serif; color: #1e293b; font-size: 8.5pt; line-height: 1.4; }
            .header { background: #0f172a; color: white; padding: 15px 20px; border-radius: 6px; margin-bottom: 15px; }
            h1 { margin: 0; font-size: 16pt; } .subtitle { margin: 3px 0 0 0; color: #94a3b8; }
            table { width: 100%; border-collapse: collapse; margin-top: 10px; background: white; }
            th { background: #1e3a8a; color: white; text-align: left; padding: 8px 10px; font-size: 8.5pt; font-weight: 600; border: 1px solid #cbd5e1; }
            td { padding: 8px 10px; border: 1px solid #e2e8f0; vertical-align: top; }
            tr:nth-child(even) { background: #f8fafc; }
            .section-lbl { font-size: 7.5pt; text-transform: uppercase; color: #64748b; font-weight: bold; display: block; margin-bottom: 2px; }
            .note-box { background-color: #f1f5f9; padding: 6px; border-left: 3px solid #0a66c2; font-style: italic; margin-top: 4px; border-radius: 2px; font-size: 8pt; }
            .pos { color: #16a34a; font-weight: bold; } .neg { color: #dc2626; font-weight: bold; }
        </style></head><body>
            <div class='header'>
                <h1>Executive Portfolio Progress Report</h1>
                <p class='subtitle'>Combined Standings Tracker & Performance Horizons Index — __HORIZON__</p>
            </div>
            <div style='display: table; width: 100%; margin-bottom: 15px;'>
                <div style='display: table-cell; background: white; border: 1px solid #cbd5e1; padding: 10px; text-align: center;'><strong>Total Reach:</strong> __TOTAL_REACH__</div>
                <div style='display: table-cell; background: white; border: 1px solid #cbd5e1; padding: 10px; text-align: center; border-left:none;'><strong>Total Output:</strong> __TOTAL_POSTS__ Posts</div>
                <div style='display: table-cell; background: white; border: 1px solid #cbd5e1; padding: 10px; text-align: center; border-left:none;'><strong>Average Portfolio SSI:</strong> __AVG_SSI__</div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th style="width: 15%;">Executive Name & Title</th>
                        <th style="width: 18%;">Follower Growth Progress</th>
                        <th style="width: 18%;">SSI Index Progress</th>
                        <th style="width: 10%;">Posts Published</th>
                        <th style="width: 14%;">Views & Appearances</th>
                        <th style="width: 25%;">Manager Performance Summary</th>
                    </tr>
                </thead>
                <tbody>__ROWS__</tbody>
            </table>
        </body></html>
        """
        rows_html = ""
        for _, row in df_source.iterrows():
            p_name = row['Profile Name']
            txt_note = st.session_state.manager_notes.get(p_name, "").strip()
            note_html = f"<div class='note-box'>{txt_note}</div>" if txt_note else "<em style='color:#94a3b8;'>No performance remarks provided.</em>"
            
            f_mom_cls = "pos" if row['Followers MoM%'] >= 0 else "neg"
            s_mom_cls = "pos" if row['SSI MoM Shift'] >= 0 else "neg"
            s_inc_cls = "pos" if row['SSI Inc Shift'] >= 0 else "neg"
            
            rows_html += f"""
            <tr>
                <td><strong>{p_name}</strong><br><span style='color:#64748b; font-size:8pt;'>{row['Job Title']}</span></td>
                <td>
                    <span class='section-lbl'>Total Base:</span> <strong>{int(row['Followers']):,}</strong><br>
                    <span class='section-lbl'>Monthly:</span> <span class='{f_mom_cls}'>{row['Followers MoM%']:+.1f}% MoM</span><br>
                    <span class='section-lbl'>Overall:</span> <span class='pos'>+{int(row['Followers Inc Growth']):,} Incept</span>
                </td>
                <td>
                    <span class='section-lbl'>Current Standing:</span> <strong>{int(row['SSI'])}/100</strong><br>
                    <span class='section-lbl'>Monthly:</span> <span class='{s_mom_cls}'>{row['SSI MoM Shift']:+g} pts MoM</span><br>
                    <span class='section-lbl'>Overall:</span> <span class='{s_inc_cls}'>{row['SSI Inc Shift']:+g} pts Incept</span>
                </td>
                <td style='text-align: center;'><strong style='font-size:12pt; color:#0a66c2;'>{int(row['Posts Published'])}</strong><br><span style='font-size:7.5pt; color:#64748b;'>Published</span></td>
                <td>
                    <span class='section-lbl'>Discovery Views:</span> <strong>{int(row['Views']):,}</strong><br>
                    <span class='section-lbl'>Search Apps:</span> <strong>{int(row['Appearances']):,}</strong>
                </td>
                <td>{note_html}</td>
            </tr>
            """
        
        final_html = html_template.replace("__ROWS__", rows_html)\
                                  .replace("__HORIZON__", selected_ym.strftime('%B %Y'))\
                                  .replace("__TOTAL_REACH__", f"{df_source['Followers'].sum():,}")\
                                  .replace("__TOTAL_POSTS__", f"{df_source['Posts Published'].sum()}")\
                                  .replace("__AVG_SSI__", f"{int(df_source['SSI'].mean())}/100")
                                  
        buf = io.BytesIO()
        HTML(string=final_html).write_pdf(buf)
        return buf.getvalue()


    try:
        team_report_bytes = generate_team_progress_pdf(df_team_standings)
        st.download_button(
            label="📥 Export Executive Portfolio Progress PDF",
            data=team_report_bytes,
            file_name=f"Executive_Portfolio_Progress_{selected_ym.strftime('%Y_%m')}.pdf",
            mime="application/pdf",
            use_container_width=True
        )
    except Exception as pdf_err:
        st.error(f"PDF Compiler Error: {pdf_err}")

    st.markdown("---")
    t_col1, t_col2, t_col3, t_col4 = st.columns(4)
    t_col1.metric("Total Follower Count", f"{df_team_standings['Followers'].sum():,} Professionals")
    t_col2.metric("Average SSI Score", f"{int(df_team_standings['SSI'].mean())}/100")
    t_col3.metric("Total Content Output", f"{df_team_standings['Posts Published'].sum()} Posts")
    t_col4.metric("Combined Active Views (Period)", f"{df_team_standings['Views'].sum():,}")

    st.markdown("---")
    st.subheader("📊 Combined Team Macro-Trend Vectors (All-Time History)")
    
    team_trends_df = df_metrics.groupby('Date').agg({
        'Total followers': 'sum',
        'Profile views': 'sum',
        'Appearances': 'sum',
        'SSI': 'mean'
    }).sort_index()
    
    tc1, tc2 = st.columns(2)
    with tc1:
        st.caption("👥 Combined Follower Growth")
        st.line_chart(team_trends_df[['Total followers']], color="#0a66c2")
        
        st.caption("🔍 Combined Platform-Wide Visibility")
        st.line_chart(team_trends_df[['Appearances']], color="#ff9900")
        
    with tc2:
        st.caption("👀 Combined Profile Views")
        st.line_chart(team_trends_df[['Profile views']], color="#1db954")
        
        st.caption("📈 Rolling Average Social Selling Index (SSI)")
        st.line_chart(team_trends_df[['SSI']], color="#dc2626")

    st.markdown("---")
    st.markdown("### 📋 Detailed Cross-Profile Leaderboard")
    display_team_df = df_team_standings.copy()
    display_team_df['Manager Remarks'] = display_team_df['Profile Name'].map(lambda x: st.session_state.manager_notes.get(x, ""))
    st.dataframe(
        display_team_df.set_index('Profile Name')[
            ['Job Title', 'Followers', 'Followers MoM%', 'Followers Inc Growth', 'SSI', 'SSI MoM Shift', 'SSI Inc Shift', 'Posts Published', 'Views', 'Appearances', 'Manager Remarks']
        ],
        use_container_width=True
    )


# ==========================================
# 🎯 TAB 2: INDIVIDUAL PROFILE DEEP DIVE
# ==========================================
with tab_individual:
    # Safely extract records matching global sidebar state variables
    prof_row = df_team_standings[df_team_standings['Profile Name'] == selected_profile].iloc[0]
    profile_metrics = df_metrics[df_metrics['Profile Name'] == selected_profile].sort_values('Date')
    current_month_data = profile_metrics[profile_metrics['YearMonth'] == selected_ym]

    st.subheader(f"📈 Strategic Progress Breakdown: {selected_profile}")
    st.markdown("---")

    ind_col_left, ind_col_right = st.columns([2, 1])
    with ind_col_right:
        st.subheader("✏️ Performance Brief Notes")
        st.session_state.manager_notes[selected_profile] = st.text_area(
            "Add context or monthly achievement statements for this user's PDF brief:",
            value=st.session_state.manager_notes[selected_profile],
            key=f"ind_notes_{selected_profile}"
        )
        
        def generate_single_progress_pdf():
            html_template = """
            <!DOCTYPE html><html><head><meta charset='utf-8'><style>
                @page { size: A4; margin: 20mm 15mm; background-color: #f8fafc; }
                body { font-family: Arial, sans-serif; color: #1e293b; font-size: 10pt; line-height: 1.5; }
                .header { background: #1e3a8a; color: white; padding: 20px; border-radius: 6px; margin-bottom: 20px; }
                h1 { margin: 0; font-size: 18pt; } .title { color: #bfdbfe; margin: 2px 0 0 0; }
                .card { background: white; padding: 16px; border: 1px solid #e2e8f0; border-top: 4px solid #2563eb; margin-bottom: 15px; border-radius: 4px; }
                .val { font-size: 22pt; font-weight: bold; color: #0f172a; margin-bottom: 5px; }
                .pos { color: #16a34a; font-weight: bold; } .neg { color: #dc2626; font-weight: bold; }
                .notes-block { background-color: #f1f5f9; padding: 15px; border-left: 4px solid #0a66c2; border-radius: 4px; margin-top: 20px; }
            </style></head><body>
                <div class='header'>
                    <h1>__NAME__</h1>
                    <p class='title'>__TITLE__ — Summary Document (__MONTH__)</p>
                </div>
                <div class='card'>
                    <div class='val'>__FOL_CURR__</div>
                    <strong>Total Audience Reach</strong><br>
                    • Monthly Delta: <span class='__FOL_MOM_CLS__'>__FOL_MOM__</span><br>
                    • Cumulative Growth (Inception): <span class='pos'>+__FOL_INC__ Followers</span>
                </div>
                <div class='card' style='border-top-color: #0d9488;'>
                    <div class='val'>__SSI_CURR__ / 100</div>
                    <strong>Social Selling Index (SSI Score)</strong><br>
                    • Monthly Delta: <span class='__SSI_MOM_CLS__'>__SSI_MOM__</span><br>
                    • Cumulative Shift (Inception): <span class='__SSI_INC_CLS__'>__SSI_INC__</span>
                </div>
                <div class='card' style='border-top-color: #64748b;'>
                    <strong>Profile Visibility & Output Metrics This Period</strong><br>
                    • Posts Published This Month: <strong>__POSTS__ Posts</strong><br>
                    • Profile Discovery Views: <strong>__VIEWS__</strong><br>
                    • Search Appearances Indexes: <strong>__APP__</strong>
                </div>
                <h2>Manager Commentary & Tactical Alignment</h2>
                <div class='notes-block'>__COMMENTARY__</div>
            </body></html>
            """
            txt = st.session_state.manager_notes.get(selected_profile, "").strip()
            comment_html = txt.replace("\n", "<br>") if txt else "<em>No remarks logged.</em>"
            
            f_mom_val = prof_row['Followers MoM%']
            s_mom_val = prof_row['SSI MoM Shift']
            s_inc_val = prof_row['SSI Inc Shift']
            
            f_html = html_template.replace('__NAME__', selected_profile)\
                                  .replace('__TITLE__', prof_row['Job Title'])\
                                  .replace('__MONTH__', selected_ym.strftime('%B %Y'))\
                                  .replace('__FOL_CURR__', f"{int(prof_row['Followers']):,}")\
                                  .replace('__FOL_MOM__', f"{f_mom_val:+.1f}% MoM")\
                                  .replace('__FOL_MOM_CLS__', 'pos' if f_mom_val >= 0 else 'neg')\
                                  .replace('__FOL_INC__', f"{int(prof_row['Followers Inc Growth']):,}")\
                                  .replace('__SSI_CURR__', f"{int(prof_row['SSI'])}")\
                                  .replace('__SSI_MOM__', f"{s_mom_val:+g} pts MoM")\
                                  .replace('__SSI_MOM_CLS__', 'pos' if s_mom_val >= 0 else 'neg')\
                                  .replace('__SSI_INC__', f"{s_inc_val:+g} pts Incept")\
                                  .replace('__SSI_INC_CLS__', 'pos' if s_inc_val >= 0 else 'neg')\
                                  .replace('__POSTS__', f"{int(prof_row['Posts Published'])}")\
                                  .replace('__VIEWS__', f"{int(prof_row['Views']):,}")\
                                  .replace('__APP__', f"{int(prof_row['Appearances']):,}")\
                                  .replace('__COMMENTARY__', comment_html)
                                  
            buf = io.BytesIO()
            HTML(string=f_html).write_pdf(buf)
            return buf.getvalue()

        try:
            single_pdf_bytes = generate_single_progress_pdf()
            st.download_button(
                label=f"📥 Download {selected_profile}'s Monthly PDF Brief",
                data=single_pdf_bytes,
                file_name=f"LinkedIn_Brief_{selected_profile.replace(' ', '_')}_{selected_ym.strftime('%Y_%m')}.pdf",
                mime="application/pdf",
                use_container_width=True
            )
        except Exception as e:
            st.error(f"Single Report Compile Error: {e}")

    with ind_col_left:
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Followers (Current)", f"{int(prof_row['Followers']):,}", f"{prof_row['Followers MoM%']:+.1f}% MoM")
        col2.metric("SSI Score", f"{int(prof_row['SSI'])}/100", f"{prof_row['SSI MoM Shift']:+g} pts MoM")
        col3.metric("Posts (This Month)", f"{int(prof_row['Posts Published'])}")
        col4.metric("Profile Views", f"{int(prof_row['Views']):,}")
        col5.metric("Search Appearances", f"{int(prof_row['Appearances']):,}")
        
        st.markdown("---")
        st.subheader("📊 Core Strategic Performance Vectors (All-Time History)")
        
        ic1, ic2 = st.columns(2)
        with ic1:
            st.caption("📈 Total Followers")
            st.line_chart(profile_metrics.set_index('Date')[['Total followers']], color="#0a66c2")
            
            st.caption("🔍 Platform-Wide Profile Appearances")
            st.line_chart(profile_metrics.set_index('Date')[['Appearances']], color="#ff9900")
            
        with ic2:
            st.caption("🛡️ Social Selling Index (SSI) Tracker")
            st.line_chart(profile_metrics.set_index('Date')[['SSI']], color="#dc2626")
            
            st.caption("👀 Profile Views")
            st.line_chart(profile_metrics.set_index('Date')[['Profile views']], color="#1db954")
            
        st.markdown("---")
        st.subheader("📝 Monthly Content Performance Logs (Historical Vectors)")
        
        individual_posts = df_posts[df_posts['Profile Name'] == selected_profile].copy()
        if not individual_posts.empty:
            monthly_posts_perf = individual_posts.groupby('YearMonth').agg({
                'Impressions': 'sum',
                'Engagement': 'sum'
            }).sort_index()
            
            monthly_posts_perf.index = monthly_posts_perf.index.astype(str)
            
            pc1, pc2 = st.columns(2)
            with pc1:
                st.caption("📈 Total Organic Post Impressions by Calendar Month")
                st.bar_chart(monthly_posts_perf['Impressions'], color="#0a66c2")
            with pc2:
                st.caption("❤️ Total Post Engagement Interactions by Calendar Month")
                st.bar_chart(monthly_posts_perf['Engagement'], color="#1db954")
        else:
            st.info("No content marketing metrics or post records exist in Airtable to map historical performance curves.")
