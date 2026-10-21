"""Business Data Assistant - Streamlit chat UI.

Run locally:  streamlit run frontend/streamlit_app.py   (API must be on API_URL)
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
EXAMPLES = [
    "Top 10 customers by revenue last quarter",
    "Show monthly revenue for the last 12 months",
    "What share of revenue comes from each customer segment?",
    "Which 5 customers owe us the most in unpaid invoices?",
    "What is the average salary by department?",
    "Mark order 4950 as delivered",
    "Create an invoice for order 4990",
]

st.set_page_config(page_title="Business Data Assistant", page_icon="📊", layout="wide")


# ----------------------------------------------------------------- API calls
def api(method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
    headers = {"Authorization": f"Bearer {st.session_state.token}"} if st.session_state.get("token") else {}
    try:
        r = httpx.request(method, f"{API_URL}{path}", headers=headers, timeout=120, **kwargs)
    except httpx.HTTPError as exc:
        return 0, {"detail": f"API unreachable at {API_URL}: {exc}"}
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"detail": r.text}


# ----------------------------------------------------------------- rendering
def render_chart(result: dict[str, Any]) -> None:
    spec = result.get("chart") or {}
    df = pd.DataFrame(result["rows"], columns=result["columns"])
    kind = spec.get("type", "table")
    if kind == "metric" and not df.empty:
        st.metric(result["columns"][0].replace("_", " ").title(), f"{df.iloc[0, 0]:,.2f}")
        return
    if kind == "line":
        fig = px.line(df, x=spec["x"], y=spec["y"], markers=True)
    elif kind == "bar":
        y = spec["y"][0]
        fig = (
            px.bar(df, y=spec["x"], x=y, orientation="h")
            if spec.get("orientation") == "h"
            else px.bar(df, x=spec["x"], y=spec["y"], barmode="group")
        )
        if spec.get("orientation") == "h":
            fig.update_layout(yaxis={"categoryorder": "total ascending"})
    elif kind == "pie":
        fig = px.pie(df, names=spec["x"], values=spec["y"][0], hole=0.4)
    else:
        return
    fig.update_layout(height=380, margin={"l": 10, "r": 10, "t": 30, "b": 10}, title=spec.get("title", ""))
    st.plotly_chart(fig, use_container_width=True)


def render_result(result: dict[str, Any], key: str) -> None:
    status = result.get("status")
    if status == "blocked":
        st.error(f"🛡️ Blocked by guardrails: {result.get('error')}")
    elif status == "cannot_answer":
        st.info(f"The data can't answer that: {result.get('error')}")
    elif status != "success":
        st.warning(f"Query failed: {result.get('error')}")
    if result.get("sql"):
        with st.expander("SQL that was run (read-only, validated)", expanded=False):
            st.code(result["sql"], language="sql")
            st.caption(
                f"Tables: {', '.join(result.get('tables_used', []))} · attempts: {result.get('attempts')} · "
                f"latency {result.get('latency_ms', 0):.0f} ms"
            )
    if status == "success":
        render_chart(result)
        df = pd.DataFrame(result["rows"], columns=result["columns"])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button("Download CSV", df.to_csv(index=False), "result.csv", "text/csv", key=f"csv-{key}")
        if result.get("truncated"):
            st.caption("Showing the first rows only (row limit).")


def render_message(msg: dict[str, Any], idx: int) -> None:
    with st.chat_message(msg["role"], avatar="🧑‍💼" if msg["role"] == "user" else "📊"):
        if msg.get("status") == "blocked":
            st.error(msg["content"])
        else:
            st.markdown(msg["content"])
        for j, result in enumerate(msg.get("ask_results", [])):
            render_result(result, f"{idx}-{j}")
        action = msg.get("pending_action")
        if action and action["action_id"] not in st.session_state.decided:
            st.warning(f"**Confirmation needed:** {action['summary']}")
            c1, c2, _ = st.columns([1, 1, 4])
            if c1.button("✅ Confirm", key=f"ok-{action['action_id']}"):
                decide(action["action_id"], True)
            if c2.button("✖ Cancel", key=f"no-{action['action_id']}"):
                decide(action["action_id"], False)


def decide(action_id: str, approve: bool) -> None:
    code, body = api("POST", f"/api/v1/actions/{action_id}/{'confirm' if approve else 'cancel'}")
    st.session_state.decided.add(action_id)
    text = body.get("message") or body.get("detail") or str(body)
    st.session_state.messages.append({"role": "assistant", "content": ("✅ " if code == 200 else "⚠️ ") + text})
    st.rerun()


def send(question: str) -> None:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.spinner("Thinking..."):
        if st.session_state.mode == "Assistant (agent + actions)":
            history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[:-1]][-8:]
            code, body = api("POST", "/api/v1/chat", json={"message": question, "history": history})
            if code == 200:
                msg = {
                    "role": "assistant",
                    "content": body["reply"],
                    "status": body["status"],
                    "ask_results": body.get("ask_results", []),
                    "pending_action": body.get("pending_action"),
                }
            else:
                msg = {"role": "assistant", "content": f"⚠️ {body.get('detail', body)}"}
        else:
            code, body = api("POST", "/api/v1/ask", json={"question": question})
            if code == 200:
                text = body.get("explanation") or ("Here is the result." if body["status"] == "success" else "")
                if body["status"] == "blocked" and body.get("reasons"):
                    text = f"Request blocked by security policy ({', '.join(body['reasons'])})."
                msg = {
                    "role": "assistant",
                    "content": text,
                    "status": body["status"],
                    "ask_results": [body] if "rows" in body else [],
                }
            else:
                msg = {"role": "assistant", "content": f"⚠️ {body.get('detail', body)}"}
    st.session_state.messages.append(msg)
    st.rerun()


# ----------------------------------------------------------------- layout
for key, default in {
    "token": None,
    "user": None,
    "messages": [],
    "decided": set(),
    "mode": "Assistant (agent + actions)",
}.items():
    st.session_state.setdefault(key, default)

with st.sidebar:
    st.title("📊 Business Data Assistant")
    if not st.session_state.token:
        st.subheader("Sign in")
        code, users = api("GET", "/auth/demo-users")
        if code != 200:
            st.error(users.get("detail", "API not reachable"))
            st.stop()
        labels = {f"{u['display_name']}": u["username"] for u in users}
        choice = st.selectbox("Demo user (one per role)", list(labels))
        st.caption(next(u["description"] for u in users if u["username"] == labels[choice]))
        password = st.text_input("Password", type="password", help="Demo password is in .env (default: demo123)")
        if st.button("Sign in", type="primary", use_container_width=True):
            code, body = api("POST", "/auth/token", json={"username": labels[choice], "password": password})
            if code == 200:
                st.session_state.token, st.session_state.user = body["access_token"], body["user"]
                st.rerun()
            st.error(body.get("detail", "Login failed"))
        st.stop()

    user = st.session_state.user
    st.success(f"Signed in as **{user['display_name']}**")
    _, me = api("GET", "/api/v1/me")
    with st.expander("What my role can do"):
        st.write("**Tables:**", ", ".join(me.get("tables", [])))
        st.write("**Tools:**", ", ".join(me.get("tools", [])))
        if me.get("hidden_columns"):
            st.write("**Hidden columns:**", "; ".join(f"{t}: {', '.join(c)}" for t, c in me["hidden_columns"].items()))
    st.session_state.mode = st.radio("Mode", ["Assistant (agent + actions)", "Direct Text-to-SQL"])
    st.markdown("**Try asking**")
    for ex in EXAMPLES:
        if st.button(ex, use_container_width=True):
            send(ex)
    c1, c2 = st.columns(2)
    if c1.button("Clear chat", use_container_width=True):
        st.session_state.messages, st.session_state.decided = [], set()
        st.rerun()
    if c2.button("Sign out", use_container_width=True):
        for k in ("token", "user"):
            st.session_state[k] = None
        st.session_state.messages = []
        st.rerun()

st.header("Ask your business data anything")
st.caption(
    "Answers come from read-only, validated SQL. Data changes always ask for your confirmation. Everything is audited."
)
for i, m in enumerate(st.session_state.messages):
    render_message(m, i)
if prompt := st.chat_input("e.g. Which products sold the most units last quarter?"):
    send(prompt)
