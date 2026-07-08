import streamlit as st
import pandas as pd
from pyairtable import Api
from datetime import datetime
import io
import requests
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
# Switched from global pyplot to pure object-oriented thread-safe Figure elements
from matplotlib.figure import Figure
import base64
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

# --- 2. CREDENTIAL AUTHENTICATION & RATE LIMIT PROTECTION ---
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

# TimeoutHTTPAdapter forces every request to fail after a bounded time instead of hanging indefinitely
class TimeoutHTTPAdapter(HTTPAdapter):
    def __init__(self, *args, timeout=30, **kwargs):
        self.timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return super().send(request, **kwargs)

# Inject the rate-limit protector + timeout directly into pyairtable's built-in session object
api.session.mount("https://", TimeoutHTTPAdapter(max_retries=retries, timeout=30))

# Define your tables using the protected API instance
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

        for metric_col in ['Total followers', 'SSI', 'Profile views', 'Appearances']:
            if metric_col in df_m.columns:
                df_m[metric_col] = pd.to_numeric(df_m[metric_col]).fillna(0)
            else:
                df_m[metric_col] = 0
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

        numeric_cols = [
            'Impressions', 'Reactions', 'Comments', 'Profile Visitors From Post',
            'Members Reached', 'Followers Gained From Post', 'Reposts', 'Saves',
            'Sends on LinkedIn', 'Decision-Maker Reach %'
        ]
        for metric_col in numeric_cols:
            if metric_col in df_p.columns:
                df_p[metric_col] = pd.to_numeric(df_p[metric_col]).fillna(0)
            else:
                df_p[metric_col] = 0

        if 'Engagement' not in df_p.columns:
            df_p['Engagement'] = df_p['Reactions'] + df_p['Comments'] + df_p['Reposts']
        else:
            df_p['Engagement'] = pd.to_numeric(df_p['Engagement']).fillna(0)

        for text_col in ['Top Target Accounts', 'Top Core Industries', 'Topic']:
            if text_col not in df_p.columns:
                df_p[text_col] = ""
            else:
                df_p[text_col] = df_p[text_col].fillna("")
    else:
        df_p = pd.DataFrame(columns=[
            'Profile Name', 'Publish Date', 'YearMonth', 'Impressions', 'Engagement',
            'Reactions', 'Comments', 'Profile Visitors From Post', 'Members Reached',
            'Followers Gained From Post', 'Reposts', 'Saves', 'Sends on LinkedIn',
            'Decision-Maker Reach %', 'Top Target Accounts', 'Top Core Industries', 'Topic'
        ])

    return df_m, df_p


# Add dynamic loading visibility to the initial data handshake
with st.spinner("⚡ Connecting to Airtable and fetching fresh portfolio metrics..."):
    try:
        df_metrics, df_posts = load_all_data()
        st.sidebar.success("⚡ Live Database Sync Active")
    except Exception as e:
        st.error(f"❌ Connection Mapping Breakpoint Encountered: {e}")
        st.stop()

# Safely establish timelines while avoiding blanks
df_metrics['YearMonth'] = df_metrics['Date'].dt.to_period('M')
available_months = sorted(df_metrics['YearMonth'].dropna().unique(), reverse=True)
all_profiles_list = sorted(df_metrics['Profile Name'].unique())

# --- 4. STATE MANAGEMENT & CROSS-TAB INPUT SYNCHRONIZATION ---
if "manager_notes" not in st.session_state:
    st.session_state.manager_notes = {}

for name in all_profiles_list:
    if name not in st.session_state.manager_notes:
        st.session_state.manager_notes[name] = ""
    if f"team_notes_{name}" not in st.session_state:
        st.session_state[f"team_notes_{name}"] = st.session_state.manager_notes[name]
    if f"ind_notes_{name}" not in st.session_state:
        st.session_state[f"ind_notes_{name}"] = st.session_state.manager_notes[name]

def sync_from_team(name):
    val = st.session_state[f"team_notes_{name}"]
    st.session_state.manager_notes[name] = val
    st.session_state[f"ind_notes_{name}"] = val

def sync_from_ind(name):
    val = st.session_state[f"ind_notes_{name}"]
    st.session_state.manager_notes[name] = val
    st.session_state[f"team_notes_{name}"] = val


# --- GLOBAL NAVIGATION CONTROL PANEL & INGESTION HUB ---
st.sidebar.title("Navigation Panel")
if not available_months:
    st.sidebar.error("❌ No valid time tracking records were parsed out from Airtable data columns.")
    st.stop()

selected_ym = st.sidebar.selectbox("📅 Reporting Horizon", available_months, format_func=lambda x: x.strftime('%B %Y'))
selected_profile = st.sidebar.selectbox("🎯 Target Professional Focus", all_profiles_list)

# --- ADVANCED POST ANALYTICS DRAG-AND-DROP INGESTION ENGINE ---
with st.sidebar.expander("📤 Post Ingestion Center"):
    st.markdown("### Process Single-Post Export")
    target_upload_profile = st.selectbox("Assign Post Data To:", all_profiles_list, key="upload_exec_select")
    uploaded_post_file = st.file_uploader("Upload LinkedIn Excel / CSV", type=["xlsx", "csv"])
    entered_topic = st.text_input("Content Topic / Context", placeholder="e.g., Q3 Keynote Address")

    if uploaded_post_file is not None:
        if st.button("🚀 Push Post to Database", use_container_width=True):
            try:
                df_upload = None
                if uploaded_post_file.name.endswith('.csv'):
                    encodings_to_try = ['utf-8', 'utf-16', 'utf-16-le', 'latin1']
                    separators_to_try = [',', '\t', ';']
                    for encoding in encodings_to_try:
                        for sep in separators_to_try:
                            try:
                                uploaded_post_file.seek(0)
                                df_test = pd.read_csv(uploaded_post_file, sep=sep, encoding=encoding)
                                if len(df_test.columns) >= 2 and len(df_test) > 5:
                                    df_upload = df_test
                                    break
                            except: continue
                        if df_upload is not None: break
                    if df_upload is None:
                        uploaded_post_file.seek(0)
                        df_upload = pd.read_csv(uploaded_post_file)
                else:
                    df_upload = pd.read_excel(uploaded_post_file)

                extracted_url = df_upload.columns[1] if len(df_upload.columns) > 1 else "Organic Post Link"
                num_cols = len(df_upload.columns)
                if num_cols >= 3:
                    df_upload.columns = ['Label', 'Value', 'Pct'] + [f'Unused_{i}' for i in range(num_cols - 3)]
                elif num_cols == 2:
                    df_upload.columns = ['Label', 'Value']
                    df_upload['Pct'] = "0%"
                else:
                    df_upload.columns = ['Label']
                    df_upload['Value'] = "0"
                    df_upload['Pct'] = "0%"

                df_upload['Label'] = df_upload['Label'].astype(str).str.strip()
                df_upload['Value'] = df_upload['Value'].astype(str).str.strip()

                def read_field(label):
                    match_row = df_upload[df_upload['Label'] == label]
                    if not match_row.empty:
                        val_str = str(match_row.iloc[0]['Value']).replace(',', '').replace(' ', '').strip()
                        try: return int(float(val_str))
                        except:
                            import re
                            digits = re.sub(r'[^\d]', '', val_str)
                            return int(digits) if digits else 0
                    return 0

                raw_date = df_upload[df_upload['Label'] == 'Post Date'].iloc[0]['Value'] if not df_upload[df_upload['Label'] == 'Post Date'].empty else datetime.today().strftime('%Y-%m-%d')
                try: clean_date = pd.to_datetime(raw_date).strftime('%Y-%m-%d')
                except: clean_date = datetime.today().strftime('%Y-%m-%d')

                companies, industries = [], []
                computed_dm_reach = 0.0
                decision_tiers = ['Director', 'VP', 'CXO', 'Owner', 'Partner']

                if 'Pct' in df_upload.columns:
                    df_upload['Pct'] = df_upload['Pct'].astype(str).str.strip()
                    for _, r in df_upload.iterrows():
                        c_label, c_val, c_pct = str(r['Label']), str(r['Value']), str(r['Pct'])
                        if c_label == 'Company': companies.append(c_val)
                        elif c_label == 'Industry': industries.append(c_val)
                        elif c_label == 'Seniority' and c_val in decision_tiers:
                            try: computed_dm_reach += float(c_pct.replace('%', ''))
                            except: pass

                all_raw_profiles = profiles_table.all()
                profile_record_id = None
                for p_rec in all_raw_profiles:
                    if p_rec['fields'].get('Full Name') == target_upload_profile:
                        profile_record_id = p_rec['id']
                        break

                payload = {
                    "Profile": [profile_record_id] if profile_record_id else [],
                    "Publish Date": clean_date,
                    "Topic": entered_topic if entered_topic else "LinkedIn Organic Content",
                    "Post URL": extracted_url,
                    "Impressions": read_field('Impressions'),
                    "Reactions": read_field('Reactions'),
                    "Comments": read_field('Comments'),
                    "Profile Visitors From Post": read_field('Profile viewers from this post'),
                    "Members Reached": read_field('Members reached'),
                    "Followers Gained From Post": read_field('Followers gained from this post'),
                    "Reposts": read_field('Reposts'),
                    "Saves": read_field('Saves'),
                    "Sends on LinkedIn": read_field('Sends on LinkedIn'),
                    "Decision-Maker Reach %": computed_dm_reach / 100.0,
                    "Top Target Accounts": ", ".join(companies[:3]),
                    "Top Core Industries": ", ".join(industries[:3])
                }

                posts_table.create(payload)
                st.sidebar.success("🎉 Ingestion complete! Refreshing...")
                st.cache_data.clear()
                st.rerun()
            except Exception as parse_ex:
                error_details = str(parse_ex)
                if hasattr(parse_ex, 'response') and parse_ex.response is not None:
                    error_details += f" | Response: {parse_ex.response.text}"
                st.sidebar.error(f"Ingestion break: {error_details}")


# --- 5. GRAPH ENGINE BASE64 EXPORT UTILITY (Thread-Isolated Figures) ---
def export_plot_to_b64(df_source, column_name, chart_type='line', color='#0a66c2'):
    if df_source.empty or column_name not in df_source.columns:
        return ""

    fig = Figure(figsize=(5.5, 2.8), facecolor='#ffffff')
    ax = fig.subplots()
    ax.set_facecolor('#ffffff')

    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#cbd5e1')
    ax.spines['bottom'].set_color('#cbd5e1')
    ax.tick_params(colors='#64748b', labelsize=8)
    ax.grid(axis='y', linestyle='--', alpha=0.5, color='#e2e8f0')

    if chart_type == 'line':
        ax.plot(df_source.index, df_source[column_name], color=color, linewidth=2, marker='o', markersize=3)
    elif chart_type == 'bar':
        ax.bar(df_source.index, df_source[column_name], color=color, alpha=0.85, width=0.6)

    img_buf = io.BytesIO()
    fig.savefig(img_buf, format='png', bbox_inches='tight', dpi=150)
    img_buf.seek(0)
    return f"data:image/png;base64,{base64.b64encode(img_buf.read()).decode('utf-8')}"


# --- 6. CACHED PDF REPORT COMPILERS ---
def generate_team_progress_pdf(df_source, trends_df, manager_notes, horizon_str):
    html_template = """
    <!DOCTYPE html><html><head><meta charset='utf-8'><style>
        @page { size: A4 landscape; margin: 10mm; background-color: #fafbfc; }
        body { font-family: sans-serif; color: #1e293b; font-size: 8.5pt; line-height: 1.4; }
        .header { background: #0f172a; color: white; padding: 15px 20px; border-radius: 6px; margin-bottom: 12px; }
        h1 { margin: 0; font-size: 16pt; } .subtitle { margin: 2px 0 0 0; color: #94a3b8; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; background: white; page-break-inside: avoid; }
        th { background: #1e3a8a; color: white; text-align: left; padding: 7px 9px; font-size: 8.5pt; font-weight: 600; border: 1px solid #cbd5e1; }
        td { padding: 7px 9px; border: 1px solid #e2e8f0; vertical-align: top; }
        tr:nth-child(even) { background: #f8fafc; }
        .section-lbl { font-size: 7.5pt; text-transform: uppercase; color: #64748b; font-weight: bold; display: block; margin-bottom: 1px; }
        .note-box { background-color: #f1f5f9; padding: 5px; border-left: 3px solid #0a66c2; font-style: italic; margin-top: 3px; border-radius: 2px; font-size: 8pt; }
        .pos { color: #16a34a; font-weight: bold; } .neg { color: #dc2626; font-weight: bold; }
        .grid-table { width: 100%; border-collapse: collapse; margin-top: 15px; background: transparent; }
        .grid-table td { border: none; padding: 6px; width: 50%; }
        .chart-card { background: white; border: 1px solid #cbd5e1; padding: 8px; border-radius: 4px; text-align: center; }
        .chart-title { font-size: 8.5pt; font-weight: bold; color: #334155; margin-bottom: 4px; text-align: left; }
        .page-break { page-break-before: always; }
    </style></head><body>
        <div class='header'>
            <h1>Executive Portfolio Progress Report</h1>
            <p class='subtitle'>Combined Standings Tracker & Performance Horizons Index — __HORIZON__</p>
        </div>
        <div style='display: table; width: 100%; margin-bottom: 12px;'>
            <div style='display: table-cell; background: white; border: 1px solid #cbd5e1; padding: 10px; text-align: center;'><strong>Total Follower Count:</strong> __TOTAL_REACH__</div>
            <div style='display: table-cell; background: white; border: 1px solid #cbd5e1; padding: 10px; text-align: center; border-left:none;'><strong>Average SSI Score:</strong> __AVG_SSI__</div>
            <div style='display: table-cell; background: white; border: 1px solid #cbd5e1; padding: 10px; text-align: center; border-left:none;'><strong>Total Pool Content Output:</strong> __TOTAL_POSTS__ Posts</div>
        </div>
        <table>
            <thead>
                <tr>
                    <th style="width: 14%;">Executive Name & Title</th>
                    <th style="width: 18%;">Follower Growth Progress</th>
                    <th style="width: 18%;">SSI Index Progress</th>
                    <th style="width: 10%;">Posts Published</th>
                    <th style="width: 15%;">Views & Appearances</th>
                    <th style="width: 25%;">Manager Performance Summary</th>
                </tr>
            </thead>
            <tbody>__ROWS__</tbody>
        </table>

        <div class="page-break"></div>
        <div class='header'>
            <h1>📊 Combined Team Macro-Trend Vectors (All-Time History)</h1>
        </div>
        <table class='grid-table'>
            <tr>
                <td>
                    <div class='chart-card'>
                        <div class='chart-title'>👥 Combined Follower Growth</div>
                        <img src='__IMG_FOL__' style='width: 100%; height: auto;'>
                    </div>
                </td>
                <td>
                    <div class='chart-card'>
                        <div class='chart-title'>👀 Combined Profile Views</div>
                        <img src='__IMG_VIEWS__' style='width: 100%; height: auto;'>
                    </div>
                </td>
            </tr>
            <tr>
                <td>
                    <div class='chart-card'>
                        <div class='chart-title'>🔍 Combined Platform-Wide Visibility</div>
                        <img src='__IMG_APP__' style='width: 100%; height: auto;'>
                    </div>
                </td>
                <td>
                    <div class='chart-card'>
                        <div class='chart-title'>📈 Rolling Average Social Selling Index (SSI)</div>
                        <img src='__IMG_SSI__' style='width: 100%; height: auto;'>
                    </div>
                </td>
            </tr>
        </table>
    </body></html>
    """
    rows_html = ""
    for _, row in df_source.iterrows():
        p_name = row['Profile Name']
        txt_note = manager_notes.get(p_name, "").strip()
        note_html = f"<div class='note-box'>{txt_note}</div>" if txt_note else "<em style='color:#94a3b8;'>No performance remarks provided.</em>"
        f_mom_cls = "pos" if row['Followers MoM%'] >= 0 else "neg"
        s_mom_cls = "pos" if row['SSI MoM Shift'] >= 0 else "neg"
        s_inc_cls = "pos" if row['SSI Inc Shift'] >= 0 else "neg"

        rows_html += f"""
        <tr>
            <td><strong>{p_name}</strong><br><span style='color:#64748b; font-size:8pt;'>{row['Job Title']}</span></td>
            <td>
                <span class='section-lbl'>Total Followers:</span> <strong>{int(row['Followers']):,}</strong><br>
                <span class='section-lbl'>Monthly:</span> <span class='{f_mom_cls}'>{row['Followers MoM%']:+.1f}% MoM</span><br>
                <span class='section-lbl'>Overall:</span> <span class='pos'>+{int(row['Followers Inc Growth']):,} since inception</span>
            </td>
            <td>
                <span class='section-lbl'>Current Standing:</span> <strong>{int(row['SSI'])}/100</strong><br>
                <span class='section-lbl'>Monthly:</span> <span class='{s_mom_cls}'>{row['SSI MoM Shift']:+g} pts MoM</span><br>
                <span class='section-lbl'>Overall:</span> <span class='{s_inc_cls}'>{row['SSI Inc Shift']:+g} pts since inception</span>
            </td>
            <td style='text-align: center;'><strong style='font-size:12pt; color:#0a66c2;'>{int(row['Posts Published'])}</strong><br><span style='font-size:7.5pt; color:#64748b;'>Published</span></td>
            <td>
                <span class='section-lbl'>Profile Views:</span> <strong>{int(row['Views']):,}</strong><br>
                <span class='section-lbl'>Profile Appearances:</span> <strong>{int(row['Appearances']):,}</strong>
            </td>
            <td>{note_html}</td>
        </tr>
        """

    b64_fol = export_plot_to_b64(trends_df, 'Total followers', 'line', '#0a66c2')
    b64_views = export_plot_to_b64(trends_df, 'Profile views', 'line', '#1db954')
    b64_app = export_plot_to_b64(trends_df, 'Appearances', 'line', '#ff9900')
    b64_ssi = export_plot_to_b64(trends_df, 'SSI', 'line', '#dc2626')

    final_html = html_template.replace("__ROWS__", rows_html)\
                              .replace("__HORIZON__", horizon_str)\
                              .replace("__TOTAL_REACH__", f"{df_source['Followers'].sum():,}")\
                              .replace("__TOTAL_POSTS__", f"{df_source['Posts Published'].sum()}")\
                              .replace("__AVG_SSI__", f"{int(df_source['SSI'].mean())}/100")\
                              .replace("__IMG_FOL__", b64_fol)\
                              .replace("__IMG_VIEWS__", b64_views)\
                              .replace("__IMG_APP__", b64_app)\
                              .replace("__IMG_SSI__", b64_ssi)

    buf = io.BytesIO()
    HTML(string=final_html).write_pdf(buf)
    return buf.getvalue()


def generate_single_progress_pdf(hist_metrics, content_df, manager_notes_str, selected_profile, job_title_str, horizon_str, f_curr, f_mom, f_inc, s_curr, s_mom, s_inc, posts_count, views_count, app_count, avg_dm_reach, accounts_str, industries_str, total_high_intent):
    html_template = """
    <!DOCTYPE html><html><head><meta charset='utf-8'><style>
        @page { size: A4; margin: 15mm 15mm; background-color: #f8fafc; }
        body { font-family: Arial, sans-serif; color: #1e293b; font-size: 10pt; line-height: 1.5; }
        .header { background: #1e3a8a; color: white; padding: 20px; border-radius: 6px; margin-bottom: 20px; }
        h1 { margin: 0; font-size: 18pt; } .title { color: #bfdbfe; margin: 2px 0 0 0; }
        .card { background: white; padding: 16px; border: 1px solid #e2e8f0; border-top: 4px solid #2563eb; margin-bottom: 15px; border-radius: 4px; }
        .val { font-size: 22pt; font-weight: bold; color: #0f172a; margin-bottom: 5px; }
        .pos { color: #16a34a; font-weight: bold; } .neg { color: #dc2626; font-weight: bold; }
        .notes-block { background-color: #f1f5f9; padding: 15px; border-left: 4px solid #0a66c2; border-radius: 4px; margin-top: 20px; }
        .grid-table { width: 100%; border-collapse: collapse; background: transparent; page-break-inside: avoid; }
        .grid-table td { border: none; padding: 5px; width: 50%; }
        .chart-card { background: white; border: 1px solid #cbd5e1; padding: 6px; border-radius: 4px; text-align: center; }
        .chart-title { font-size: 8pt; font-weight: bold; color: #475569; margin-bottom: 3px; text-align: left; }
        .page-break { page-break-before: always; }
    </style></head><body>
        <div class='header'>
            <h1>__NAME__</h1>
            <p class='title'>__TITLE__ — Summary Document (__MONTH__)</p>
        </div>
        <div class='card'>
            <div class='val'>__FOL_CURR__</div>
            <strong>Total Audience Reach</strong><br>
            • Monthly Delta: <span class='__FOL_MOM_CLS__'>__FOL_MOM__</span><br>
            • Cumulative Growth (Since Inception): <span class='pos'>+__FOL_INC__ Followers</span>
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

        <div class='card' style='border-top-color: #7c3aed;'>
            <strong>Audience Quality & Account Intelligence Index</strong><br>
            • Average Decision-Maker Reach Tier: <strong>__DM_REACH__%</strong><br>
            • Key Target Accounts Engaged: <em>__TARGET_ACCOUNTS__</em><br>
            • Primary Industry Heatmaps: <em>__TARGET_INDUSTRIES__</em><br>
            • Content High-Intent Signals: <strong>__SAVED_SHARED__ Actions (Saves/Sends/Reposts)</strong>
        </div>

        <h2>Manager Commentary & Tactical Alignment</h2>
        <div class='notes-block'>__COMMENTARY__</div>

        <div class="page-break"></div>
        <div class='header'><h1>📊 Core Strategic Performance Vectors (All-Time History)</h1></div>
        <table class='grid-table'>
            <tr>
                <td>
                    <div class='chart-card'>
                        <div class='chart-title'>📈 Total Followers</div>
                        <img src='__CHART_FOL__' style='width:100%; height:auto;'>
                    </div>
                </td>
                <td>
                    <div class='chart-card'>
                        <div class='chart-title'>🛡️ Social Selling Index (SSI) Tracker</div>
                        <img src='__CHART_SSI__' style='width:100%; height:auto;'>
                    </div>
                </td>
            </tr>
            <tr>
                <td>
                    <div class='chart-card'>
                        <div class='chart-title'>🔍 Platform-Wide Profile Appearances</div>
                        <img src='__CHART_APP__' style='width:100%; height:auto;'>
                    </div>
                </td>
                <td>
                    <div class='chart-card'>
                        <div class='chart-title'>👀 Profile Views</div>
                        <img src='__CHART_VIEWS__' style='width:100%; height:auto;'>
                    </div>
                </td>
            </tr>
        </table>

        __CONTENT_SECTION__
    </body></html>
    """
    comment_html = manager_notes_str.replace("\n", "<br>") if manager_notes_str else "<em>No remarks logged.</em>"
    b64_ind_fol = export_plot_to_b64(hist_metrics.set_index('Date'), 'Total followers', 'line', '#0a66c2')
    b64_ind_ssi = export_plot_to_b64(hist_metrics.set_index('Date'), 'SSI', 'line', '#dc2626')
    b64_ind_app = export_plot_to_b64(hist_metrics.set_index('Date'), 'Appearances', 'line', '#ff9900')
    b64_ind_views = export_plot_to_b64(hist_metrics.set_index('Date'), 'Profile views', 'line', '#1db954')

    content_section_html = ""
    if not content_df.empty:
        monthly_posts_perf = content_df.groupby('YearMonth').agg({'Impressions': 'sum', 'Engagement': 'sum'}).sort_index()
        monthly_posts_perf.index = monthly_posts_perf.index.astype(str)
        b64_post_imp = export_plot_to_b64(monthly_posts_perf, 'Impressions', 'bar', '#0a66c2')
        b64_post_eng = export_plot_to_b64(monthly_posts_perf, 'Engagement', 'bar', '#1db954')

        content_section_html = f"""
        <div class="page-break"></div>
        <div class='header'><h1>📝 Monthly Content Performance Logs (Historical Vectors)</h1></div>
        <table class='grid-table'>
            <tr>
                <td>
                    <div class='chart-card'>
                        <div class='chart-title'>📈 Total Organic Post Impressions by Calendar Month</div>
                        <img src='{b64_post_imp}' style='width:100%; height:auto;'>
                    </div>
                </td>
                <td>
                    <div class='chart-card'>
                        <div class='chart-title'>❤️ Total Post Engagement Interactions by Calendar Month</div>
                        <img src='{b64_post_eng}' style='width:100%; height:auto;'>
                    </div>
                </td>
            </tr>
        </table>
        """

    f_html = html_template.replace('__NAME__', selected_profile)\
                          .replace('__TITLE__', job_title_str)\
                          .replace('__MONTH__', horizon_str)\
                          .replace('__FOL_CURR__', f"{int(f_curr):,}")\
                          .replace('__FOL_MOM__', f"{f_mom:+.1f}% MoM")\
                          .replace('__FOL_MOM_CLS__', 'pos' if f_mom >= 0 else 'neg')\
                          .replace('__FOL_INC__', f"{int(f_inc):,}")\
                          .replace('__SSI_CURR__', f"{int(s_curr)}")\
                          .replace('__SSI_MOM__', f"{s_mom:+g} pts MoM")\
                          .replace('__SSI_MOM_CLS__', 'pos' if s_mom >= 0 else 'neg')\
                          .replace('__SSI_INC__', f"{s_inc:+g} pts Since inception")\
                          .replace('__SSI_INC_CLS__', 'pos' if s_inc >= 0 else 'neg')\
                          .replace('__POSTS__', f"{int(posts_count)}")\
                          .replace('__VIEWS__', f"{int(views_count):,}")\
                          .replace('__APP__', f"{int(app_count):,}")\
                          .replace('__DM_REACH__', f"{avg_dm_reach:.1f}")\
                          .replace('__TARGET_ACCOUNTS__', accounts_str)\
                          .replace('__TARGET_INDUSTRIES__', industries_str)\
                          .replace('__SAVED_SHARED__', f"{int(total_high_intent)}")\
                          .replace('__COMMENTARY__', comment_html)\
                          .replace('__CHART_FOL__', b64_ind_fol)\
                          .replace('__CHART_SSI__', b64_ind_ssi)\
                          .replace('__CHART_APP__', b64_ind_app)\
                          .replace('__CHART_VIEWS__', b64_ind_views)\
                          .replace('__CONTENT_SECTION__', content_section_html)

    buf = io.BytesIO()
    HTML(string=f_html).write_pdf(buf)
    return buf.getvalue()


# --- 7. CROSS-PROFILE LEADERBOARD STANDINGS ---
def compute_profile_standings(df_metrics, df_posts, all_profiles_list, selected_ym):
    rows = []
    for name in all_profiles_list:
        pm = df_metrics[df_metrics['Profile Name'] == name].sort_values('Date')
        if pm.empty:
            continue

        job_title = pm['Job Title'].iloc[-1] if 'Job Title' in pm.columns else 'Executive'
        first = pm.iloc[0]

        current_rows = pm[pm['YearMonth'] == selected_ym]
        current = current_rows.iloc[-1] if not current_rows.empty else pm.iloc[-1]

        prev_rows = pm[pm['YearMonth'] == (selected_ym - 1)]
        prev = prev_rows.iloc[-1] if not prev_rows.empty else None

        followers_curr = current['Total followers']
        followers_prev = prev['Total followers'] if prev is not None else followers_curr
        followers_mom = ((followers_curr - followers_prev) / followers_prev * 100) if followers_prev else 0.0
        followers_inc = followers_curr - first['Total followers']

        ssi_curr = current['SSI']
        ssi_prev = prev['SSI'] if prev is not None else ssi_curr
        ssi_mom = ssi_curr - ssi_prev
        ssi_inc = ssi_curr - first['SSI']

        posts_count = 0
        if not df_posts.empty:
            posts_count = len(df_posts[(df_posts['Profile Name'] == name) & (df_posts['YearMonth'] == selected_ym)])

        rows.append({
            'Profile Name': name,
            'Job Title': job_title,
            'Followers': followers_curr,
            'Followers MoM%': followers_mom,
            'Followers Inc Growth': followers_inc,
            'SSI': ssi_curr,
            'SSI MoM Shift': ssi_mom,
            'SSI Inc Shift': ssi_inc,
            'Posts Published': posts_count,
            'Views': current['Profile views'],
            'Appearances': current['Appearances'],
        })

    return pd.DataFrame(rows)


df_team_standings = compute_profile_standings(df_metrics, df_posts, all_profiles_list, selected_ym)

# --- 8. TAB LAYOUT ---
tab_team, tab_individual = st.tabs(["👥 Team Overview", "🎯 Individual Deep Dive"])


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
                st.text_area(
                    f"Notes for {name} ({selected_ym.strftime('%b %Y')}):",
                    key=f"team_notes_{name}",
                    on_change=sync_from_team,
                    args=(name,)
                )

    team_trends_df = df_metrics.groupby('Date').agg({
        'Total followers': 'sum',
        'Profile views': 'sum',
        'Appearances': 'sum',
        'SSI': 'mean'
    }).sort_index()

    if st.button("🛠️ Compile Portfolio PDF Report"):
        try:
            team_report_bytes = generate_team_progress_pdf(
                df_team_standings, team_trends_df, st.session_state.manager_notes, selected_ym.strftime('%B %Y')
            )
            st.session_state.compiled_team_pdf = team_report_bytes
            st.success("✨ Report compilation complete!")
        except Exception as pdf_err:
            st.error(f"PDF Compiler Error: {pdf_err}")

    if "compiled_team_pdf" in st.session_state:
        st.download_button(
            label="📥 Download Executive Portfolio Progress PDF",
            data=st.session_state.compiled_team_pdf,
            file_name=f"Executive_Portfolio_Progress_{selected_ym.strftime('%Y_%m')}.pdf",
            mime="application/pdf",
            use_container_width=True
        )

    st.markdown("---")
    t_col1, t_col2, t_col3, t_col4 = st.columns(4)
    t_col1.metric("Total Follower Count", f"{df_team_standings['Followers'].sum():,} ")
    t_col2.metric("Average SSI Score", f"{int(df_team_standings['SSI'].mean())}/100")
    t_col3.metric("Total Content Output", f"{df_team_standings['Posts Published'].sum()} Posts")
    t_col4.metric("Combined Active Views (Period)", f"{df_team_standings['Views'].sum():,}")

    st.markdown("---")
    st.subheader("📊 Combined Team Macro-Trend Vectors (All-Time History)")

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
    matching_profile_rows = df_team_standings[df_team_standings['Profile Name'] == selected_profile] if not df_team_standings.empty else pd.DataFrame()

    if matching_profile_rows.empty:
        st.info(f"📅 No metrics tracking matrices could be mapped out for **{selected_profile}** during this reporting horizon. Add history records in Airtable or process an organic post export to activate this space.")
    else:
        prof_row = matching_profile_rows.iloc[0]
        profile_metrics = df_metrics[df_metrics['Profile Name'] == selected_profile].sort_values('Date')
        current_month_data = profile_metrics[profile_metrics['YearMonth'] == selected_ym]

        individual_posts = df_posts[df_posts['Profile Name'] == selected_profile].copy()
        month_posts = individual_posts[individual_posts['YearMonth'] == selected_ym] if not individual_posts.empty else pd.DataFrame()

        avg_dm_reach = month_posts['Decision-Maker Reach %'].mean() * 100 if not month_posts.empty and 'Decision-Maker Reach %' in month_posts.columns else 0.0
        total_saves = month_posts['Saves'].sum() if not month_posts.empty and 'Saves' in month_posts.columns else 0
        total_sends = month_posts['Sends on LinkedIn'].sum() if not month_posts.empty and 'Sends on LinkedIn' in month_posts.columns else 0
        total_reposts = month_posts['Reposts'].sum() if not month_posts.empty and 'Reposts' in month_posts.columns else 0
        total_high_intent = total_saves + total_sends + total_reposts

        accounts_seen = [str(x) for x in month_posts['Top Target Accounts'].dropna().unique() if str(x) != ""]
        accounts_summary_str = ", ".join(accounts_seen)[:100] if accounts_seen else "No corporate target tracking entries logged."

        industries_seen = [str(x) for x in month_posts['Top Core Industries'].dropna().unique() if str(x) != ""]
        industries_summary_str = ", ".join(industries_seen)[:100] if industries_seen else "No industrial tracking profiles mapped."

        st.subheader(f"📈 Strategic Progress Breakdown: {selected_profile}")
        st.markdown("---")

        ind_col_left, ind_col_right = st.columns([2, 1])
        with ind_col_right:
            st.subheader("✏️ Performance Brief Notes")
            st.text_area(
                "Add context or monthly achievement statements for this user's PDF brief:",
                key=f"ind_notes_{selected_profile}",
                on_change=sync_from_ind,
                args=(selected_profile,)
            )

            if st.button(f"🛠️ Prepare {selected_profile}'s Monthly Brief"):
                try:
                    single_pdf_bytes = generate_single_progress_pdf(
                        profile_metrics, individual_posts,
                        st.session_state.manager_notes.get(selected_profile, ""),
                        selected_profile, prof_row['Job Title'], selected_ym.strftime('%B %Y'),
                        prof_row['Followers'], prof_row['Followers MoM%'], prof_row['Followers Inc Growth'],
                        prof_row['SSI'], prof_row['SSI MoM Shift'], prof_row['SSI Inc Shift'],
                        prof_row['Posts Published'], prof_row['Views'], prof_row['Appearances'],
                        avg_dm_reach, accounts_summary_str, industries_summary_str, total_high_intent
                    )
                    # Namespaced profile storage block to fix crossover downloads cache leak bugs
                    st.session_state[f"compiled_single_pdf_{selected_profile}"] = single_pdf_bytes
                    st.success("✨ Dossier completed successfully!")
                except Exception as e:
                    st.error(f"Single Report Compile Error: {e}")

            if f"compiled_single_pdf_{selected_profile}" in st.session_state:
                st.download_button(
                    label=f"📥 Download {selected_profile}'s Monthly PDF Brief",
                    data=st.session_state[f"compiled_single_pdf_{selected_profile}"],
                    file_name=f"LinkedIn_Brief_{selected_profile.replace(' ', '_')}_{selected_ym.strftime('%Y_%m')}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )

        with ind_col_left:
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Followers (Current)", f"{int(prof_row['Followers']):,}", f"{prof_row['Followers MoM%']:+.1f}% MoM")
            col2.metric("SSI Score", f"{int(prof_row['SSI'])}/100", f"{prof_row['SSI MoM Shift']:+g} pts MoM")
            col3.metric("Posts (This Month)", f"{int(prof_row['Posts Published'])}")
            col4.metric("Profile Views", f"{int(prof_row['Views']):,}")
            col5.metric("Search Appearances", f"{int(prof_row['Appearances']):,}")

            st.markdown("### 🎯 Audience Quality & Account Intelligence Index")
            aq_col1, aq_col2, aq_col3 = st.columns(3)
            aq_col1.metric("Avg. Decision-Maker Reach", f"{avg_dm_reach:.1f}%", help="Percentage of readers carrying Director, VP, CXO, Owner, or Partner corporate hierarchy titles.")
            aq_col2.metric("High-Intent Shares & Saves", f"{int(total_high_intent)} Actions", help="Sum total of bookmarks, direct internal messages, and user reposts.")
            with aq_col3:
                st.markdown(f"**Top Target Accounts Reached:**\n`{accounts_summary_str}`")

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

                st.markdown("### 📋 Granular Post Performance Tracking")
                if not month_posts.empty:
                    display_posts = month_posts.copy()
                    display_posts['DM Reach'] = display_posts['Decision-Maker Reach %'].map(lambda x: f"{x*100:.1f}%")
                    st.dataframe(
                        display_posts[['Publish Date', 'Topic', 'Impressions', 'Reactions', 'Comments', 'Reposts', 'Saves', 'Sends on LinkedIn', 'DM Reach', 'Top Target Accounts']],
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.info("No single-post organic analytics have been uploaded for this reporting block yet.")
            else:
                st.info("No content marketing metrics or post records exist in Airtable to map historical performance curves.")
