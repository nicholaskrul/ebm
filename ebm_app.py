import streamlit as st
import pandas as pd
from pyairtable import Api
from datetime import datetime
import io
from weasyprint import HTML

st.set_page_config(page_title='LinkedIn Executive Analytics', page_icon='💼', layout='wide', initial_sidebar_state='expanded')

st.markdown('<style>[data-testid="stMetricValue"] { font-size: 28px; font-weight: bold; } [data-testid="stMetricDelta"] { font-size: 14px; }</style>', unsafe_allow_html=True)

AIRTABLE_TOKEN = st.secrets.get('AIRTABLE_TOKEN')
BASE_ID = st.secrets.get('BASE_ID')

if not AIRTABLE_TOKEN or not BASE_ID:
    st.error('❌ Configuration Missing!')
    st.stop()

api = Api(AIRTABLE_TOKEN)
profiles_table = api.table(BASE_ID, 'Profiles')
metrics_table = api.table(BASE_ID, 'Weekly Metrics')
posts_table = api.table(BASE_ID, 'Posts and content')

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
        p_ids = fields.get('Profile', [])
        fields['Profile Name'] = id_to_name.get(p_ids[0], 'Unassigned') if p_ids else 'Unassigned'
        fields['Job Title'] = id_to_title.get(p_ids[0], 'Executive') if p_ids else 'Executive'
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
        p_ids = fields.get('Profile', [])
        fields['Profile Name'] = id_to_name.get(p_ids[0], 'Unassigned') if p_ids else 'Unassigned'
        posts_data.append(fields)
    df_p = pd.DataFrame(posts_data)
    if not df_p.empty and 'Publish Date' in df_p.columns:
        df_p['Publish Date'] = pd.to_datetime(df_p['Publish Date'])
    else:
        df_p = pd.DataFrame(columns=['Profile Name', 'Publish Date', 'Topic', 'Post URL', 'Impressions', 'Engagement'])
    return df_m, df_p

df_metrics, df_posts = load_all_data()
tab_individual, tab_team = st.tabs(['🎯 Individual Deep Dive', '👥 Team Overview Leaderboard'])

with tab_individual:
    st.sidebar.title('Navigation Panel')
    if df_metrics.empty:
        st.warning('No metrics found.')
    else:
        all_profiles = sorted(df_metrics['Profile Name'].unique())
        selected_profile = st.sidebar.selectbox('🎯 Target Professional', all_profiles)
        df_metrics['YearMonth'] = df_metrics['Date'].dt.to_period('M')
        available_months = sorted(df_metrics['YearMonth'].unique(), reverse=True)
        selected_ym = st.sidebar.selectbox('📅 Report Horizon', available_months, format_func=lambda x: x.strftime('%B %Y'))
        profile_metrics = df_metrics[df_metrics['Profile Name'] == selected_profile].sort_values('Date')
        current_month_data = profile_metrics[profile_metrics['YearMonth'] == selected_ym]
        prev_month_data = profile_metrics[profile_metrics['YearMonth'] == (selected_ym - 1)]
        kpis = {'followers': (0, 0), 'views': (0, 0), 'appearances': (0, 0), 'ssi': (0, 0)}
        inception_data = {'followers_growth': 0, 'ssi_growth': 0}
        job_title = 'Executive'
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
                if is_absolute_diff: return curr_val, curr_val - base_val
                else: return curr_val, ((curr_val - base_val) / base_val * 100) if base_val else 0
            kpis['followers'] = calc_delta('Total followers')
            kpis['views'] = calc_delta('Profile views')
            kpis['appearances'] = calc_delta('Appearances')
            kpis['ssi'] = calc_delta('SSI', is_absolute_diff=True)
            inception_data['followers_growth'] = int(latest_row.get('Total followers', 0) - earliest_row.get('Total followers', 0))
            inception_data['ssi_growth'] = int(latest_row.get('SSI', 0) - earliest_row.get('SSI', 0))
        
        def generate_pdf_report():
            html = "<!DOCTYPE html><html><head><style>@page { size: A4; margin: 20mm 15mm; } body { font-family: sans-serif; color: #1e293b; } .header { background: #1e3a8a; color: white; padding: 20px; border-radius: 6px; } .card { background: white; padding: 15px; border: 1px solid #e2e8f0; border-top: 4px solid #2563eb; margin-top: 15px; }</style></head><body><div class='header'><h1>"+selected_profile+"</h1><p>"+job_title+" - "+selected_ym.strftime('%B %Y')+"</p></div><div class='card'><strong>Total Followers:</strong> "+f"{int(kpis['followers'][0]):,}"+" (MoM: "+f"{kpis['followers'][1]:+.1f}%"+")<br>Growth Since Inception: +"+f"{inception_data['followers_growth']:,}"+"</div><div class='card' style='border-top-color: #0d9488;'><strong>SSI Score:</strong> "+f"{int(kpis['ssi'][0])}"+"/100 (MoM Shift: "+f"{kpis['ssi'][1]:+g} pts"+")<br>Shift Since Inception: "+f"{inception_data['ssi_growth']:+g} pts"+"</div><div class='card' style='border-top-color: #64748b;'><p><strong>Profile Discovery Views:</strong> "+f"{int(kpis['views'][0]):,}"+"</p><p><strong>Search Appearances Queries:</strong> "+f"{int(kpis['appearances'][0]):,}"+"</p></div></body></html>"
            pdf_buffer = io.BytesIO()
            HTML(string=html).write_pdf(pdf_buffer)
            return pdf_buffer.getvalue()
        
        st.subheader(f'📈 Performance Analysis: {selected_profile}')
        st.sidebar.markdown('---')
        st.sidebar.subheader('🗂️ Individual Report')
        if not current_month_data.empty:
            try:
                pdf_data = generate_pdf_report()
                st.sidebar.download_button(label='📥 Export Monthly PDF Report', data=pdf_data, file_name=f'LinkedIn_Report_{selected_profile}.pdf', mime='application/pdf', use_container_width=True)
            except Exception as e:
                st.sidebar.error(f'PDF Error: {e}')
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric('Total Audience Reach', f"{int(kpis['followers'][0]):,}", f"{kpis['followers'][1]:+.1f}% MoM")
        col2.metric('Profile Discovery Views', f"{int(kpis['views'][0]):,}", f"{kpis['views'][1]:+.1f}% MoM")
        col3.metric('Search Appearances', f"{int(kpis['appearances'][0]):,}", f"{kpis['appearances'][1]:+.1f}% MoM")
        col4.metric('Social Selling Index', f"{int(kpis['ssi'][0])}/100", f"{kpis['ssi'][1]:+g} pts MoM")
        st.markdown('---')
        chart_col, table_col = st.columns([2, 1])
        with chart_col:
            st.subheader('📈 Long-Term Growth Vector')
            st.line_chart(profile_metrics.set_index('Date')[['Total followers']], color='#0a66c2')
        with table_col:
            st.subheader('📋 Core Data Logs This Period')
            if not current_month_data.empty:
                display_raw = current_month_data[['Date', 'Total followers', 'Profile views', 'Appearances', 'SSI']].copy()
                st.dataframe(display_raw.set_index('Date'), use_container_width=True)

with tab_team:
    st.subheader('👥 Combined Team Performance Hub')
    st.markdown('Real-time comparative summary across all managed executive profiles.')
    if not df_metrics.empty:
        current_time = pd.Timestamp.now().normalize()
        thirty_days_ago = current_time - pd.Timedelta(days=30)
        if not df_posts.empty and 'Publish Date' in df_posts.columns:
            recent_posts = df_posts[df_posts['Publish Date'] >= thirty_days_ago]
            post_counts_series = recent_posts.groupby('Profile Name').size()
        else: post_counts_series = pd.Series(dtype=int)
        
        latest_indices = df_metrics.groupby('Profile Name')['Date'].idxmax()
        team_summary_df = df_metrics.loc[latest_indices].copy()
        team_summary_df['Posts (Past 30 Days)'] = team_summary_df['Profile Name'].map(post_counts_series).fillna(0).astype(int)
        pdf_summary = team_summary_df.sort_values(by='Total followers', ascending=False).copy()
        
        def generate_team_pdf_report(df_data):
            html = "<!DOCTYPE html><html><head><style>@page { size: A4 landscape; margin: 15mm 12mm; } body { font-family: sans-serif; color: #1e293b; } .header { background: #0f172a; color: white; padding: 20px; border-radius: 6px; } table { width: 100%; border-collapse: collapse; margin-top: 20px; } th { background: #1e3a8a; color: white; padding: 10px; text-align: left; } td { padding: 10px; border-bottom: 1px solid #e2e8f0; } tr:nth-child(even) { background: #f8fafc; } .metrics { display: table; width: 100%; margin-bottom: 15px; } .card { display: table-cell; background: #f1f5f9; padding: 10px; text-align: center; border: 1px solid #cbd5e1; }</style></head><body><div class='header'><h1>Managed Executive Portfolio Summary</h1><p>Active Combined Team Standings Leaderboard</p></div><div class='metrics'><div class='card'><strong>Total Reach:</strong> "+f"{df_data['Total followers'].sum():,}"+"</div><div class='card'><strong>Total Post Volume (30d):</strong> "+f"{df_data['Posts (Past 30 Days)'].sum()}"+"</div><div class='card'><strong>Max SSI Standing:</strong> "+f"{int(df_data['SSI'].max())}/100"+"</div></div><table><thead><tr><th>Executive Name</th><th>Job Title</th><th>Followers</th><th>Posts (30d)</th><th>SSI Score</th><th>Profile Views</th><th>Search Apps</th></tr></thead><tbody>"
            for _, row in df_data.iterrows():
                html += f"<tr><td><strong>{row['Profile Name']}</strong></td><td>{row['Job Title']}</td><td>{int(row['Total followers']):,}</td><td>{int(row['Posts (Past 30 Days)'])}</td><td>{int(row['SSI'])}/100</td><td>{int(row['Profile views']):,}</td><td>{int(row['Appearances']):,}</td></tr>"
            html += "</tbody></table></body></html>"
            pdf_buffer = io.BytesIO()
            HTML(string=html).write_pdf(pdf_buffer)
            return pdf_buffer.getvalue()
        
        try:
            team_pdf_bytes = generate_team_pdf_report(pdf_summary)
            st.download_button(label='📥 Export Combined Team PDF Report', data=team_pdf_bytes, file_name='LinkedIn_Combined_Team_Report.pdf', mime='application/pdf')
        except Exception as e:
            st.error(f'Team PDF Error: {e}')
        
        st.markdown('---')
        team_summary_df = team_summary_df.rename(columns={'Profile Name': 'Executive Name', 'Total followers': 'Followers Count', 'Profile views': 'Profile Views', 'Appearances': 'Search Appearances', 'Date': 'Last Updated Log'})
        team_summary_df = team_summary_df.sort_values(by='Followers Count', ascending=False)
        
        team_col1, team_col2, team_col3 = st.columns(3)
        team_col1.metric('Total Combined Managed Pool Reach', f"{team_summary_df['Followers Count'].sum():,}")
        team_col2.metric('Total Content Actions (Past 30 Days)', f"{team_summary_df['Posts (Past 30 Days)'].sum()} Posts")
        team_col3.metric('Highest Team SSI Standing', f"{int(team_summary_df['SSI'].max())}/100")
        st.dataframe(team_summary_df.set_index('Executive Name')[['Followers Count', 'Posts (Past 30 Days)', 'SSI', 'Profile Views', 'Search Appearances', 'Job Title']], use_container_width=True)
