"""
app.py — a simple web page for asking questions.
Run with: streamlit run app.py
"""

import asyncio
import streamlit as st
from client import ask

st.title("Database Investigator")

question = st.text_input("Ask a question about your database:")

if st.button("Ask") and question:
    with st.spinner("Thinking..."):
        
        try:
            answer = asyncio.run(ask(question))
            if answer.startswith("ERROR:"):
                st.error(answer.replace("ERROR:", "").strip())
            else:
                st.write(answer)
        except Exception as e:
            st.error(f"Error while contacting AI model: {e}")