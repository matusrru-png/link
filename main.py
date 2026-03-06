"""
LINK – Flask webapp
===================
Použitie:
    pip install flask psycopg2-binary python-dotenv
    python app.py

.env:
    DATABASE_URL=postgresql://...neon...?sslmode=require
    ACCOUNT_NUMBER=1234567890/0800
"""

import os
import re
import base64
from datetime import datetime
from flask import Flask, render_template_string, redirect, request
import requests as req
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

ACCOUNT_NUMBER = os.getenv("ACCOUNT_NUMBER", "— číslo účtu nenastavené —")

CAT_DLH  = {"PŮJČENÉ (ref)"}
CAT_VRAT = {"VRÁCENO (ref)"}

# ------------------------------------------------------------
# Neon HTTP API
# ------------------------------------------------------------

def _neon_creds():
    url = os.getenv("DATABASE_URL", "")
    m = re.match(r"postgresql://([^:]+):([^@]+)@([^/]+)/([^?]+)", url)
    if not m:
        raise ValueError("Nesprávny formát DATABASE_URL")
    return m.group(1), m.group(2), m.group(3), m.group(4)

def neon_query(sql: str, params: list = None):
    user, password, host, dbname = _neon_creds()
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    if params:
        for i in range(len(params)):
            sql = sql.replace("%s", f"${i+1}", 1)
    payload = {"query": sql}
    if params:
        payload["params"] = [str(p) for p in params]
    resp = req.post(
        f"https://{host}/sql",
        json=payload,
        headers={
            "Authorization": f"Basic {token}",
            "Neon-Connection-String": f"postgresql://{user}:{password}@{host}/{dbname}?sslmode=require",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("rows", [])

def get_person_name(person_id: int):
    rows = neon_query("SELECT name FROM persons WHERE id = %s", [person_id])
    return rows[0]["name"] if rows else None

def get_transactions(person_id: int):
    rows = neon_query("""
        SELECT
            t.date,
            t.description,
            t.amount,
            t.currency,
            t.amount_czk,
            er.rate_to_czk  AS kurz,
            c.name          AS category
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        LEFT JOIN exchange_rates er
               ON er.date = t.date AND er.currency = t.currency
        WHERE t.person_id = %s
        ORDER BY t.date DESC
    """, [person_id])
    result = []
    for r in rows:
        result.append({
            "date":        datetime.strptime(r["date"][:10], "%Y-%m-%d").date() if r["date"] else None,
            "description": r["description"] or "",
            "amount":      float(r["amount"] or 0),
            "currency":    r["currency"] or "",
            "amount_czk":  float(r["amount_czk"] or 0),
            "kurz":        float(r["kurz"]) if r.get("kurz") else None,
            "category":    r.get("category"),
        })
    return result

def compute_summary(rows):
    dlh  = sum(abs(r["amount_czk"]) for r in rows if r["category"] in CAT_DLH)
    vrat = sum(abs(r["amount_czk"]) for r in rows if r["category"] in CAT_VRAT)
    return dlh, vrat, dlh - vrat

# ------------------------------------------------------------
# Templates
# ------------------------------------------------------------

LANDING = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LINK</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500&family=IBM+Plex+Mono:wght@400&display=swap" rel="stylesheet">
<style>
  :root{--bg:#0a0a0a;--surface:#111;--border:#1e1e1e;--green:#00e599;--green-dim:rgba(0,229,153,.12);--text:#ededed;--muted:#888;}
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%;background:var(--bg);color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-weight:300}
  .wrap{min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:40px}
  .logo{font-family:'IBM Plex Mono',monospace;font-size:.8rem;font-weight:400;letter-spacing:.2em;color:var(--green);margin-bottom:48px}
  .desc{font-size:1rem;color:var(--muted);line-height:1.7;margin-bottom:36px;text-align:center;max-width:360px}
  label{font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:10px;display:block}
  .row{display:flex;border:1px solid var(--border);background:var(--surface);transition:border-color .2s;width:320px}
  .row:focus-within{border-color:var(--green);box-shadow:0 0 0 3px var(--green-dim)}
  .row input{flex:1;background:transparent;border:none;outline:none;padding:14px 16px;font-family:'IBM Plex Mono',monospace;font-size:1.1rem;color:var(--text);letter-spacing:.15em;width:100%}
  .row input::placeholder{color:var(--border)}
  .row button{background:var(--green);border:none;color:#0a0a0a;padding:14px 20px;font-family:'IBM Plex Sans',sans-serif;font-size:.75rem;font-weight:500;letter-spacing:.1em;text-transform:uppercase;cursor:pointer;transition:opacity .15s;white-space:nowrap}
  .row button:hover{opacity:.85}
  .error{font-size:.75rem;color:#ff6b6b;margin-top:12px;text-align:center}
  @media(max-width:400px){.wrap{padding:24px}.row{width:100%}}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">LINK</div>
  <p class="desc">Enter your ID to view your transaction summary and outstanding balance.</p>
  <div>
    <label for="pid">Your ID</label>
    <div class="row">
      <input type="number" id="pid" placeholder="0000" autocomplete="off" inputmode="numeric"/>
      <button onclick="go()">View →</button>
    </div>
    {% if error %}<p class="error">{{ error }}</p>{% endif %}
  </div>
</div>
<script>
  function go(){const v=document.getElementById('pid').value.trim();if(v) window.location.href='/vypis/'+v;}
  document.getElementById('pid').addEventListener('keydown',e=>{if(e.key==='Enter') go();});
</script>
</body></html>"""


VYPIS = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LINK</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;500&family=IBM+Plex+Mono:wght@400&display=swap" rel="stylesheet">
<style>
  :root{--bg:#0a0a0a;--surface:#111;--border:#1e1e1e;--green:#00e599;--red:#ff6b6b;--text:#ededed;--muted:#888;}
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  html,body{background:var(--bg);color:var(--text);font-family:'IBM Plex Sans',sans-serif;font-weight:300}
  .wrap{max-width:960px;margin:0 auto;padding:40px 40px 80px}
  header{display:flex;justify-content:space-between;align-items:center;padding-bottom:28px;border-bottom:1px solid var(--border);margin-bottom:48px}
  .logo{font-family:'IBM Plex Mono',monospace;font-size:.8rem;letter-spacing:.2em;color:var(--green)}
  .back{font-size:.75rem;color:var(--muted);text-decoration:none;transition:color .2s}
  .back:hover{color:var(--green)}

  .summary{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--border);border:1px solid var(--border);margin-bottom:32px}
  .card{background:var(--surface);padding:24px 20px}
  .card-label{font-size:.65rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:10px}
  .card-value{font-size:1.5rem;font-weight:500;letter-spacing:-.01em}
  .card-value.green{color:var(--green)}
  .card-value.red{color:var(--red)}
  .card-sub{font-size:.65rem;color:var(--muted);margin-top:4px}

  .account-box{border:1px solid var(--border);background:var(--surface);padding:18px 20px;margin-bottom:48px;display:flex;justify-content:space-between;align-items:center}
  .account-label{font-size:.65rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}
  .account-number{font-family:'IBM Plex Mono',monospace;font-size:.95rem;font-weight:400;color:var(--green);letter-spacing:.08em}
  .account-hint{font-size:.7rem;color:var(--muted)}

  .table-header{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:14px}
  .table-title{font-size:.65rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
  .table-count{font-size:.65rem;color:var(--muted)}
  table{width:100%;border-collapse:collapse}
  thead tr{border-bottom:1px solid var(--border)}
  th{font-size:.62rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);padding:10px 12px;text-align:left;font-weight:400}
  th:nth-child(3),th:nth-child(6),td:nth-child(3),td:nth-child(6){text-align:right}
  tbody tr{border-bottom:1px solid var(--border);transition:background .15s}
  tbody tr:hover{background:var(--surface)}
  td{padding:11px 12px;font-size:.82rem;color:var(--text)}
  td.date{font-family:'IBM Plex Mono',monospace;font-size:.75rem;color:var(--muted);white-space:nowrap}
  td.desc{max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  td.num{font-family:'IBM Plex Mono',monospace;font-size:.78rem;white-space:nowrap;text-align:right}
  td.pos{color:var(--green)}
  td.neg{color:var(--red)}

  @media(max-width:700px){
    .wrap{padding:24px 20px 60px}
    .summary{grid-template-columns:1fr 1fr}
    td.desc{max-width:120px}
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">LINK</div>
    <a class="back" href="/">← back</a>
  </header>

  <div class="summary">
    <div class="card">
      <div class="card-label">ID</div>
      <div class="card-value" style="font-family:'IBM Plex Mono',monospace;font-size:1.2rem">{{ person_id }}</div>
    </div>
    <div class="card">
      <div class="card-label">Total lent</div>
      <div class="card-value green">{{ "%.2f"|format(dlh) }}</div>
      <div class="card-sub">CZK</div>
    </div>
    <div class="card">
      <div class="card-label">Balance due</div>
      <div class="card-value {% if rozdiel > 0 %}red{% else %}green{% endif %}">{{ "%.2f"|format(rozdiel) }}</div>
      <div class="card-sub">CZK {% if rozdiel <= 0 %}— settled ✓{% endif %}</div>
    </div>
  </div>

  {% if rozdiel > 0 %}
  <div class="account-box">
    <div>
      <div class="account-label">Send payment to</div>
      <div class="account-number">{{ account_number }}</div>
    </div>
    <div class="account-hint">{{ "%.2f"|format(rozdiel) }} CZK due</div>
  </div>
  {% endif %}

  <div class="table-header">
    <div class="table-title">Transactions</div>
    <div class="table-count">{{ visible_rows|length }} records</div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Date</th>
        <th>Description</th>
        <th>Amount</th>
        <th>Currency</th>
        <th>Rate</th>
        <th>CZK</th>
      </tr>
    </thead>
    <tbody>
      {% for r in visible_rows %}
      <tr>
        <td class="date">{{ r.date.strftime('%d.%m.%Y') }}</td>
        <td class="desc" title="{{ r.description }}">{{ r.description }}</td>
        <td class="num {% if r.amount >= 0 %}pos{% else %}neg{% endif %}">{{ "%.2f"|format(r.amount) }}</td>
        <td style="font-size:.75rem;color:var(--muted)">{{ r.currency }}</td>
        <td class="num" style="color:var(--muted)">{{ "%.4f"|format(r.kurz) if r.kurz else '—' }}</td>
        <td class="num {% if r.amount_czk >= 0 %}pos{% else %}neg{% endif %}">{{ "%.2f"|format(r.amount_czk) }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
</body></html>"""

# ------------------------------------------------------------
# Routes
# ------------------------------------------------------------

@app.route('/')
def index():
    error = request.args.get('error')
    return render_template_string(LANDING, error=error)

@app.route('/vypis/<int:person_id>')
def vypis(person_id):
    name = get_person_name(person_id)
    if not name:
        return redirect('/?error=ID+not+found.+Please+check+your+number.')

    rows = get_transactions(person_id)
    dlh, vrat, rozdiel = compute_summary(rows)

    # Z tabuľky skryjeme PŮJČENÉ a VRÁCENO – kamarát ich nemusí vidieť
    visible_rows = rows

    return render_template_string(VYPIS,
        person_id=person_id,
        rows=rows,
        visible_rows=visible_rows,
        dlh=dlh,
        vrat=vrat,
        rozdiel=rozdiel,
        account_number=ACCOUNT_NUMBER,
    )

# ------------------------------------------------------------

if __name__ == '__main__':
    app.run(debug=True)
