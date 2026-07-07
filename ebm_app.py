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

st.markdown('''
<style>
    [data-testid="stMetricValue"] { font-size: 26px; font-weight: bold; }
    [data-testid="stMetricDelta"] { font-size: 13px; }
    .comment-box { border-left: 4px solid #0a66c2; padding-left: 15px; margin: 10px 0; background-color: #f4f6f9; padding: 10px; border-radius: 4px; }
</style>
''', unsafe_allow_html=True)

# --- 2. STATE MANAGEMENT FOR INTERACTIVE COMMENTS ---
# Caches text input strings in memory so selections or tab changes don't wipe your typed notes
if "manager_notes" not in st.session_state:
    st.session_state.manager_notes = {}

# --- 3. CREDENTIAL AUTHENTICATION ---
AIRTABLE_TOKEN = st.secrets.get("AIRTABLE_TOKEN")
BASE_ID = st.secrets.get("BASE_ID")

if not AIRTABLE_TOKEN or not BASE_ID:
    st.error("❌ Configuration Missing! Define your `AIRTABLE_TOKEN` and `BASE_ID` inside your secret management dashboard.")
    st.stop()

api = Api(AIRTABLE_TOKEN)
profiles_table = api.table(BASE_ID, "Profiles")
metrics_table = api.table(BASE_ID, "Weekly Metrics")


# --- 4. DATA RECONCILIATION & PIPELINE ENGINE ---
@st.cache_data(ttl=600)
def load_all_data():
    raw_profiles = profiles_table.all()
    raw_metrics = metrics_table.all()
    
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
        
    return df_m


try:
    df_metrics = load_all_data()
    st.sidebar.success("⚡ Live Database Sync Active")
except Exception as e:
    st.error(f"⚠️ Connection Mapping Breakpoint Encountered: {e}")
    st.stop()

if df_metrics.empty:
    st.warning("Database setup confirmed, but no metric records were detected.")
    st.stop()

# Build timeline filters globally
df_metrics['YearMonth'] = df_metrics['Date'].dt.to_period('M')
available_months = sorted(df_metrics['YearMonth'].unique(), reverse=True)

# Global configuration selectors located in the sidebar panel
st.sidebar.title("Navigation Panel")
selected_ym = st.sidebar.selectbox("📅 Reporting Horizon", available_months, format_func=lambda x: x.strftime('%B %Y'))

# --- 5. COMPREHENSIVE TEAM METRICS METRIC CALCULATOR ---
# Automatically extracts data variations, loops through every individual profile, and measures MoM and Inception status codes
all_profiles_list = sorted(df_metrics['Profile Name'].unique())
team_records = []

for name in all_profiles_list:
    prof_df = df_metrics[df_metrics['Profile Name'] == name].sort_values('Date')
    if prof_df.empty: continue
    
    c_month = prof_df[prof_df['YearMonth'] == selected_ym]
    p_month = prof_df[prof_df['YearMonth'] == (selected_ym - 1)]
    
    # Baseline defaults
    earliest = prof_df.iloc[0]
    latest = c_month.iloc[-1] if not c_month.empty else prof_df.iloc[-1]
    baseline = p_month.iloc[-1] if not p_month.empty else (c_month.iloc[0] if not c_month.empty else earliest)
    
    # Clean null extraction variables
    f_curr, f_base, f_early = latest.get('Total followers', 0), baseline.get('Total followers', 0), earliest.get('Total followers', 0)
    s_curr, s_base, s_early = latest.get('SSI', 0), baseline.get('SSI', 0), earliest.get('SSI', 0)
    v_curr = latest.get('Profile views', 0)
    a_curr = latest.get('Appearances', 0)
    
    # Math engines
    fol_mom = ((f_curr - f_base) / f_base * 100) if f_base else 0
    fol_inc = f_curr - f_early
    ssi_mom = s_curr - s_base
    ssi_inc = s_curr - s_early
    
    # Store clean tracking parameters
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
        'Date': latest['Date']
    })

df_team_standings = pd.DataFrame(team_records)

# Initialize blank placeholder strings inside cache keys if not already present
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
    st.markdown("Aggregated standings with cross-profile analytics tracking overall and monthly data pipelines side by side.")
    st.markdown("---")
    
    # Expandable input interface for typewriter commentaries
    with st.expander("📝 Edit Executive Monthly Commentary Notes"):
        st.info("The insights you enter below will automatically render directly inside the interactive master table and any generated PDF documents.")
        cmt_cols = st.columns(2)
        for idx, name in enumerate(all_profiles_list):
            target_col = cmt_cols[0] if idx % 2 == 0 else cmt_cols[1]
            with target_col:
                st.session_state.manager_notes[name] = st.text_area(
                    f"Notes for {name} ({selected_ym.strftime('%b %Y')}):",
                    value=st.session_state.manager_notes[name],
                    key=f"team_notes_{name}"
                )
                
    # Function generating the wide, descriptive matrix view for printing
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
            <table>
                <thead>
                    <tr>
                        <th style="width: 18%;">Executive Name & Title</th>
                        <th style="width: 20%;">Follower Growth Progress</th>
                        <th style="width: 20%;">SSI Index Progress</th>
                        <th style="width: 12%;">Profile Views</th>
                        <th style="width: 12%;">Search Apps</th>
                        <th style="width: 18%;">Manager Performance Summary</th>
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
                <td><strong style='font-size:10pt;'>{int(row['Views']):,}</strong></td>
                <td><strong style='font-size:10pt;'>{int(row['Appearances']):,}</strong></td>
                <td>{note_html}</td>
            </tr>
            """
        
        final_html = html_template.replace("__ROWS__", rows_html).replace("__HORIZON__", selected_ym.strftime('%B %Y'))
        buf = io.BytesIO()
        HTML(string=final_html).write_pdf(buf)
        return buf.getvalue()

    # Layout generation controls
    try:
        team_report_bytes = generate_team_progress_pdf(df_team_standings)
        st.download_button(
            label="📥 Export Executive Portfolio Progress PDF",
            data=team_report_bytes,
            file_name=f"Executive_Portfolio_Progress_{selected_ym.strftime('%Y_%m')}.pdf",
            mime="application/pdf"
        )
    except Exception as pdf_err:
        st.error(f"PDF Compiler Error: {pdf_err}")

    # Metrics Layout Panel
    st.markdown("---")
    t_col1, t_col2, t_col3 = st.columns(3)
    t_col1.metric("Total Managed Core Network Pool", f"{df_team_standings['Followers'].sum():,} Professionals")
    t_col2.metric("Portfolio Average SSI Standing", f"{int(df_team_standings['SSI'].mean())}/100")
    t_col3.metric("Combined Active Views (Period)", f"{df_team_standings['Views'].sum():,}")

    # Process local presentation data frame parameters
    st.markdown("### 📊 Consolidated Standings Matrix Grid")
    display_team_df = df_team_standings.copy()
    display_team_df['Manager Remarks'] = display_team_df['Profile Name'].map(lambda x: st.session_state.manager_notes.get(x, ""))
    
    st.dataframe(
        display_team_df.set_index('Profile Name')[
            ['Job Title', 'Followers', 'Followers MoM%', 'Followers Inc Growth', 'SSI', 'SSI MoM Shift', 'SSI Inc Shift', 'Views', 'Appearances', 'Manager Remarks']
        ],
        use_container_width=True
    )


# ==========================================
# 🎯 TAB 2: INDIVIDUAL PROFILE DEEP DIVE
# ==========================================
with tab_individual:
    selected_profile = st.selectbox("🎯 Target Professional Profiles Focus", all_profiles_list)
    
    # Local tracking subsets
    prof_row = df_team_standings[df_team_standings['Profile Name'] == selected_profile].iloc[0]
    profile_metrics = df_metrics[df_metrics['Profile Name'] == selected_profile].sort_values('Date')
    current_month_data = profile_metrics[profile_metrics['YearMonth'] == selected_ym]

    st.subheader(f"📈 Strategic Progress Breakdown: {selected_profile}")
    st.markdown(f"Deep-dive performance indices mapped across **{selected_ym.strftime('%B %Y')}** timeline benchmarks.")
    st.markdown("---")

    # Layout Row: Individual text input box
    ind_col_left, ind_col_right = st.columns([2, 1])
    with ind_col_right:
        st.subheader("✏️ Performance Brief Notes")
        st.session_state.manager_notes[selected_profile] = st.text_area(
            "Add context, action logs, or monthly achievement statements for this user's PDF brief:",
            value=st.session_state.manager_notes[selected_profile],
            key=f"ind_notes_{selected_profile}"
        )
        
        # Individual report engine matching parameters perfectly
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
                    <p class='title'>__TITLE__ — Executive Performance Summary (__MONTH__)</p>
                </div>
                
                <div class='card'>
                    <div class='val'>__FOL_CURR__</div>
                    <strong>Total Audience Reach</strong><br>
                    • Monthly Delta: <span class='__FOL_MOM_CLS__'>__FOL_MOM__</span><br>
                    • Cumulative Growth (Inception-to-Date): <span class='pos'>+__FOL_INC__ Net Followers</span>
                </div>
                
                <div class='card' style='border-top-color: #0d9488;'>
                    <div class='val'>__SSI_CURR__ <span style='font-size:12pt; color:#64748b; font-weight:normal;'>/ 100</span></div>
                    <strong>Social Selling Index (SSI Score)</strong><br>
                    • Monthly Delta: <span class='__SSI_MOM_CLS__'>__SSI_MOM__</span><br>
                    • Cumulative Shift (Inception-to-Date): <span class='__SSI_INC_CLS__'>__SSI_INC__</span>
                </div>
                
                <div class='card' style='border-top-color: #64748b;'>
                    <strong>Profile Visibility Metrics This Period</strong><br>
                    • Profile Discovery Views: <strong>__VIEWS__</strong><br>
                    • Search Appearances Indexes: <strong>__APP__</strong>
                </div>
                
                <h2>Manager Commentary & Tactical Alignment</h2>
                <div class='notes-block'>__COMMENTARY__</div>
            </body></html>
            """
            txt = st.session_state.manager_notes.get(selected_profile, "").strip()
            comment_html = txt.replace("\n", "<br>") if txt else "<em style='color:#64748b;'>No performance remarks provided for this operational timeline.</em>"
            
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
        # Screen Performance Blocks
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Followers (Current)", f"{int(prof_row['Followers']):,}", f"{prof_row['Followers MoM%']:+.1f}% MoM")
        col2.metric("SSI Score", f"{int(prof_row['SSI'])}/100", f"{prof_row['SSI MoM Shift']:+g} pts MoM")
        col3.metric("Profile Views", f"{int(prof_row['Views']):,}")
        col4.metric("Search Appearances", f"{int(prof_row['Appearances']):,}")
        
        st.markdown("---")
        st.subheader("📈 Historical Growth Trajectory Baseline")
        st.line_chart(profile_metrics.set_index('Date')[['Total followers']], color="#0a66c2")
