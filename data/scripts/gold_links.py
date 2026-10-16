"""Compute gold 'linked warning' pairs from the CAROL exports under the strict definition.

Definition (use everywhere):
  A recommendation is a LINKED WARNING for a crash when all three hold:
    1. The NTSB repeated (Recommendation Reiteration) or reclassified (Report Reclassification)
       it in that crash's own report. A 'Recommendation Mention' alone does NOT count.
    2. It was issued before the crash. Issue date = the earliest of the origin investigation's
       BoardMeetingDate/ReportDate, the earliest correspondence date or DateClosed and 31 Dec of the ID year,
       ignoring dates before 1 Jan of the ID year.
    3. It was still open on the crash date (DateClosed null or after the crash).
Run: python gold_links.py <recs.json> <investigations_with_recs.json> <pool.json> > gold_links.json
"""
import json, re, sys, collections as C

recs = json.load(open(sys.argv[1]))
inv = json.load(open(sys.argv[2]))
pool = json.load(open(sys.argv[3]))

A = {r['NTSBNumber']: r for r in pool if r.get('Mode') == 'Aviation'}
A.update({r['NTSBNumber']: r for r in inv})
norm = lambda s: re.sub(r'[^A-Z0-9]', '', (s or '').upper())
by_report = {norm(r.get('ReportNumber')): r for r in A.values() if r.get('ReportNumber')}
RPT = re.compile(r'\b[A-Z]{2,4}-\d{2}-\d{2}\b')
LINK_TYPES = ('Recommendation Reiteration', 'Report Reclassification')

def dedupe_letters(addressee):
    seen, out = set(), []
    for c in addressee.get('Correspondence', []):
        k = (c.get('CorrespondenceDate'), c.get('CorrespondenceType'), (c.get('ResponseSummary') or '')[:120])
        if k not in seen:
            seen.add(k); out.append(c)
    return out

def issue_date(r):
    # Each candidate is an upper bound: early recs precede the final report, and no letter precedes the rec.
    # The ID year is the issue year (A-25-001 -> 2025); candidates before it are bad data.
    y = int(r['Srid'].split('-')[1]); y = 1900 + y if y >= 60 else 2000 + y
    cands = [(f'{y}-12-31', 2, 'id')]
    for n in r['Notations']:
        o = A.get(n['NtsbNum']) or A.get(re.sub(r'(?<=\d)[A-Z]+$', '', n['NtsbNum']))  # CEN19MA141AB -> CEN19MA141
        if o:
            for k in ('BoardMeetingDate', 'ReportDate'):
                v = (o.get(k) or '')[:10]
                if v and not v.startswith('0001'):
                    cands.append((v, 0, 'report'))
    cands += [(c['CorrespondenceDate'][:10], 1, 'letter') for a in r['Addressees'] for c in a['Correspondence'] if c.get('CorrespondenceDate')]
    if r.get('DateClosed'):
        cands.append((r['DateClosed'][:10], 1, 'letter'))  # the closing letter, even when the export lacks it
    d, _, how = min(c for c in cands if c[0] >= f'{y}-01-01')
    return d, how

links = C.defaultdict(dict)
for r in recs:
    origin = {n['NtsbNum'] for n in r['Notations']}
    idt, how = issue_date(r)
    closed = (r.get('DateClosed') or '')[:10]
    for a in r['Addressees']:
        for c in dedupe_letters(a):
            if c['CorrespondenceType'] not in LINK_TYPES: continue
            for m in RPT.finditer(c['ResponseSummary'] or ''):
                L = by_report.get(norm(m.group(0)))
                if not L or L['NTSBNumber'] in origin: continue
                ld = (L.get('EventDateTimeUTC') or '')[:10]
                if ld and idt < ld and (not closed or closed > ld):
                    links[L['NTSBNumber']][r['Srid']] = {'issued': idt, 'issued_from': how, 'via': c['CorrespondenceType'], 'letter_date': c['CorrespondenceDate'][:10]}

out = []
for k, v in links.items():
    a = A[k]
    out.append({'ntsb_num': k, 'event_date': a['EventDateTimeUTC'][:10], 'city': a.get('City'), 'fatal': a.get('FatalInjuryCount') or 0,
                'report': a.get('ReportNumber'), 'lat': a.get('Latitude'), 'lon': a.get('Longitude'), 'warnings': v})
out.sort(key=lambda x: x['event_date'])
json.dump({'definition': __doc__.strip(), 'crashes': len(out), 'links': sum(len(x['warnings']) for x in out), 'items': out}, sys.stdout, indent=1)
