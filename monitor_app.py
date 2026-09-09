"""Standalone monitoring dashboard, also accessible through the original app."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from monitoring.config import DEFAULT_CONFIG, ROOT, load_config, save_config
from monitoring.store import connect

st.set_page_config(page_title='Campaign Guardian • Daily monitor',page_icon='✓',layout='wide')
st.markdown('''<style>
[data-testid="stAppViewContainer"]{background:#f5f7fa}
.block-container{max-width:1440px;padding-top:2.3rem}
h1,h2,h3{color:#12352b}
[data-testid="stMetric"]{background:white;border:1px solid #dde4e3;border-radius:12px;padding:16px}
.stButton>button[kind="primary"]{background:#067a46;border-color:#067a46}
</style>''',unsafe_allow_html=True)
root=Path(os.environ.get('QA_DATA_DIR',ROOT/'monitor-data'))
config_path=Path(os.environ.get('QA_CONFIG',DEFAULT_CONFIG))
db=connect(root)
try:
    cfg=load_config(config_path)
except Exception as exc:
    st.error(f'Configuration needs attention: {exc}')
    st.stop()

st.caption('CAMPAIGN GUARDIAN / WEBSITE QA')
left,right=st.columns([4,1])
with left:
    st.title('Daily funnel monitor')
    st.write('HelloFresh · Chef’s Plate · Factor')
with right:
    if st.button('Run checks now',type='primary',use_container_width=True):
        with st.spinner('Running Playwright checks. Results are saved as each journey completes…'):
            proc=subprocess.run([sys.executable,'-m','monitoring.runner','--config',str(config_path),'--data-dir',str(root)],cwd=ROOT,capture_output=True,text=True)
        if proc.returncode==2:
            st.error(proc.stdout or proc.stderr)
        else:
            st.success('Run finished. Results are available below.')
    if st.button('Refresh results',use_container_width=True):
        st.rerun()

runs=pd.read_sql_query('SELECT id,started,finished,status FROM runs ORDER BY started DESC LIMIT 200',db)
issues=pd.read_sql_query('SELECT * FROM issues ORDER BY last_seen DESC',db)
heartbeat=db.execute('SELECT * FROM scheduler WHERE id=1').fetchone()
last=runs.iloc[0] if len(runs) else None
columns=st.columns(4)
columns[0].metric('Latest run',last['status'].upper() if last is not None else 'NOT RUN')
columns[1].metric('Open issues',len(issues[issues.status=='Open']) if len(issues) else 0)
columns[2].metric('Enabled campaigns',sum(c.get('enabled',True) for c in cfg['campaigns']))
columns[3].metric('Daily check time',cfg['daily_at'])
if heartbeat:
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(heartbeat['heartbeat'])).total_seconds()
    running=last is not None and last['status']=='running'
    st.caption(f"Schedule: {cfg['timezone']} · Last heartbeat: {heartbeat['heartbeat']} · {heartbeat['message']}")
    if age>120 and not running:
        st.warning('The scheduler heartbeat is stale. Check that the scheduler process is running.')
else:
    st.info('No local scheduler heartbeat. GitHub Actions scheduling is managed separately in the repository Actions tab. You can run local checks manually above.')
if any(not c.get('steps') or not c.get('voucher',{}).get('enabled') for c in cfg['campaigns'] if c.get('enabled',True)):
    st.warning('Coverage is incomplete: configure the campaign funnel steps and active vouchers. The starter configuration checks landing pages only.')

overview,issue_tab,settings=st.tabs(['Run results','Issue log','Campaign setup'])
with overview:
    if not len(runs):
        st.subheader('Ready for your first check')
        st.write('Review your campaign URLs and settings, then select Run checks now. No sample results are shown.')
    else:
        selected=st.selectbox('Run',runs.id.tolist(),format_func=lambda x:f"{x} · {runs.set_index('id').loc[x,'status']}")
        results=db.execute('SELECT report FROM results WHERE run_id=?',(selected,)).fetchall()
        if not results:
            row=db.execute('SELECT summary FROM runs WHERE id=?',(selected,)).fetchone()
            st.info('No completed journeys in this run.')
            if row and row['summary']:
                st.code(row['summary'],language='json')
        for row in results:
            report=json.loads(row['report'])
            with st.expander(f"{report['brand']} / {report['device']} — {report['status'].upper()}",expanded=True):
                st.caption(f"{report['url']} · {report['attempts']} attempt(s)")
                frame=pd.DataFrame(report['checks'])
                st.dataframe(frame.reindex(columns=['title','status','expected','actual','retry']),hide_index=True,use_container_width=True)
                st.download_button('Download journey JSON',json.dumps(report,indent=2),file_name=f"{selected}-{report['campaign']}-{report['device']}.json",key=f"export-{report['campaign']}-{report['device']}")
        if len(runs)>1:
            st.subheader('Run history')
            st.dataframe(runs,hide_index=True,use_container_width=True)
with issue_tab:
    if not len(issues):
        st.write('No documented issues yet. Check Run results to confirm coverage and whether checks have run.')
    else:
        a,b=st.columns(2)
        brand=a.selectbox('Brand',['All']+sorted(issues.brand.unique().tolist()))
        status=b.selectbox('Issue status',['Open','Resolved','All'])
        filtered=issues.copy()
        if brand!='All': filtered=filtered[filtered.brand==brand]
        if status!='All': filtered=filtered[filtered.status==status]
        visible=filtered.drop(columns=['evidence'])
        st.dataframe(visible,hide_index=True,use_container_width=True)
        st.download_button('Export issue log CSV',visible.to_csv(index=False),file_name='campaign-qa-issues.csv',mime='text/csv')
        for _,issue in filtered.iterrows():
            with st.expander(f"{issue['severity']} · {issue['brand']} · {issue['title']} · {issue['device']}"):
                ev=json.loads(issue['evidence'])
                st.write('**Expected:**',ev.get('expected',''))
                st.write('**Actual:**',ev.get('actual',''))
                if ev.get('retry'): st.write(ev['retry'])
                st.caption(f"First seen {issue['first_seen']} · Last seen {issue['last_seen']} · {issue['occurrences']} run(s)")
                for kind in ('screenshot','trace'):
                    file=Path(ev.get(kind,''))
                    if ev.get(kind) and file.is_file() and file.resolve().is_relative_to(root.resolve()):
                        if kind=='screenshot': st.image(str(file))
                        else: st.download_button('Download Playwright trace',file.read_bytes(),file_name='trace.zip',key=issue['fingerprint'])
with settings:
    st.subheader('Local schedule')
    st.caption('These settings control the optional local scheduler. Edit .github/workflows/daily-monitor.yml to change the GitHub Actions schedule.')
    with st.form('schedule'):
        daily=st.text_input('Daily time (24-hour HH:MM)',cfg['daily_at'])
        zone=st.text_input('Timezone',cfg['timezone'])
        if st.form_submit_button('Save schedule'):
            try:
                cfg['daily_at']=daily;cfg['timezone']=zone
                save_config(cfg,config_path);st.success('Saved. The running scheduler will pick up the new time.');st.rerun()
            except Exception as exc: st.error(str(exc))
    st.subheader('Campaigns')
    chosen=st.selectbox('Choose a campaign',range(len(cfg['campaigns'])),format_func=lambda i:cfg['campaigns'][i]['name'])
    c=cfg['campaigns'][chosen]
    with st.form(f'campaign-{chosen}'):
        enabled=st.checkbox('Enabled',c.get('enabled',True))
        url=st.text_input('Starting URL or campaign link',c['url'])
        devices=st.multiselect('Browser layouts',['desktop','mobile'],default=c['devices'])
        limit=st.number_input('Page readiness limit (milliseconds)',min_value=1,max_value=120000,value=int(c['load_limit_ms']))
        ready=st.text_input('Landing page ready selector',c['ready_selector'],help='Replace body with your main CTA selector to measure usability. Body only measures initial page readiness.')
        st.caption('Use Playwright codegen to identify selectors. Interactive steps must declare a ready_selector for the result. Stop before account creation, subscription or payment unless your team has provided a dedicated test flow.')
        steps=st.text_area('Funnel steps (JSON)',json.dumps(c.get('steps',[]),indent=2),height=220)
        v=c.get('voucher',{})
        voucher_enabled=st.checkbox('Test an active voucher',v.get('enabled',False))
        code=st.text_input('Voucher code',v.get('code',''))
        first=st.text_input('Valid from (YYYY-MM-DD)',v.get('valid_from',''))
        until=st.text_input('Valid until (YYYY-MM-DD)',v.get('valid_until',''))
        eligible=st.checkbox('The configured customer and plan qualify for this voucher',v.get('eligibility_confirmed',False))
        inp=st.text_input('Voucher input selector',v.get('input_selector',''))
        apply=st.text_input('Apply button selector',v.get('apply_selector',''))
        result=st.text_input('Discount result selector',v.get('result_selector',''))
        expected=st.text_input('Exact expected discount text',v.get('expected_text',''))
        if st.form_submit_button('Save campaign',type='primary'):
            try:
                cfg['campaigns'][chosen]={**c,'enabled':enabled,'url':url,'devices':devices,'load_limit_ms':limit,'ready_selector':ready,'steps':json.loads(steps),'voucher':dict(enabled=voucher_enabled,code=code,valid_from=first,valid_until=until,eligibility_confirmed=eligible,input_selector=inp,apply_selector=apply,result_selector=result,expected_text=expected)}
                save_config(cfg,config_path);st.success('Campaign saved.');st.rerun()
            except Exception as exc: st.error(f'Could not save: {exc}')
    st.caption('To add more campaigns, copy a campaign in monitor-config.json and give it a unique id.')
db.close()
