"""Streamlit-App: Datei hochladen, Kennzahlen berechnen und von Claude einen Bericht schreiben lassen.

Starten mit:
    streamlit run app.py
"""

from io import BytesIO
from xml.sax.saxutils import escape

import anthropic
import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from kennzahlen_erzeugung import compute_kpis, load_data

MODEL = "claude-sonnet-5-5"


def build_summary(kpis: dict) -> str:
    """Fasst die Kennzahlen als Text zusammen, den Claude lesen kann."""
    start, end = kpis["period"]
    # Zeitpunkte als Text, damit die Zahlen-Rundung nur auf Zahlen angewendet wird
    stats = kpis["stats"].copy()
    stats["Zeitpunkt Maximum"] = stats["Zeitpunkt Maximum"].dt.strftime("%d.%m.%Y %H:%M")
    stats = stats.round(1)
    share_min, share_min_time = kpis["renewable_share_min"]
    share_max, share_max_time = kpis["renewable_share_max"]
    total_max, total_max_time = kpis["total_max_mw"]

    return f"""Zeitraum mit vollständigen Daten: {start:%d.%m.%Y %H:%M} bis {end:%d.%m.%Y %H:%M}
Land: Deutschland
Anzahl 15-Minuten-Intervalle: {kpis['n_intervals']}

Anteil erneuerbarer Energie (energiegewichtet): {kpis['renewable_share_energy']:.1%}
Anteil erneuerbarer Energie (Mittel über Intervalle): {kpis['renewable_share_mean']:.1%}
Minimum des Anteils: {share_min:.1%} am {share_min_time:%d.%m.%Y %H:%M}
Maximum des Anteils: {share_max:.1%} am {share_max_time:%d.%m.%Y %H:%M}

Gesamterzeugung Mittelwert: {kpis['total_mean_mw']:,.0f} MW
Gesamterzeugung Maximum: {total_max:,.0f} MW am {total_max_time:%d.%m.%Y %H:%M}
Gesamterzeugung Energie: {kpis['total_energy_twh']:.2f} TWh

Pro Energieträger (Mittelwert in MW, Maximum in MW mit Zeitpunkt, Energie in GWh):
{stats.to_string()}
"""


def ask_claude(api_key: str, summary: str) -> str:
    """Schickt die Zusammenfassung an die Claude API und gibt den Bericht zurück."""
    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        "Du bist Nachhaltigkeitsberater und schreibst einen ESG-Bericht auf Deutsch "
        "(Umwelt-Teil, Schwerpunkt Stromerzeugung). Gliedere den Bericht in diese Abschnitte: "
        "Zusammenfassung, Kennzahlen zur Energiewende, Trends, Auffälligkeiten, Ausblick und Fazit. "
        "Formulierung sachlich und förmlich, etwa 300 Wörter. "
        "Nutze nur die Zahlen unten und erfinde keine Werte. Erkläre Fachbegriffe wie ESG kurz, "
        "denn die Leserin ist Anfängerin. Weise am Ende darauf hin, dass sich der Bericht nur auf "
        "die Daten im Datensatz bezieht und keine vollständige Nachhaltigkeitsbewertung ist.\n\n"
        f"Kennzahlen:\n{summary}"
    )
    response = client.messages.create(
        model=MODEL,
        # Großzügiges Limit: Denkschritte zählen mit, und ein zu kleines Limit schneidet den Text ab
        max_tokens=6000,
        messages=[{"role": "user", "content": prompt}],
    )
    # Die Antwort besteht aus mehreren Teilen. Manche sind interne Denkschritte ohne Text,
    # deshalb sammeln wir nur die Teile, die echten Text enthalten.
    text = "".join(block.text for block in response.content if block.type == "text")
    if response.stop_reason == "max_tokens":
        text += "\n\n(Hinweis: Die Antwort wurde abgeschnitten. Bitte erneut erstellen.)"
    return text


def report_to_pdf(report: str) -> bytes:
    """Schreibt den Berichtstext in eine PDF-Datei und gibt deren Inhalt zurück."""
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Bericht: Stromerzeugung in Deutschland", styles["Title"]),
        Spacer(1, 12),
    ]
    for line in report.splitlines():
        text = line.strip()
        if not text:
            story.append(Spacer(1, 6))
        elif text.startswith("#"):
            # Überschriften aus dem Markdown-Text (z. B. "## Trends") werden zu fetten Titeln
            story.append(Paragraph(escape(text.lstrip("#").strip()), styles["Heading2"]))
        elif text.startswith(("- ", "* ")):
            story.append(Paragraph("• " + escape(text[2:].replace("**", "")), styles["BodyText"]))
        else:
            story.append(Paragraph(escape(text.replace("**", "")), styles["BodyText"]))

    buffer = BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=50, rightMargin=50, topMargin=50, bottomMargin=50).build(story)
    return buffer.getvalue()


def main() -> None:
    st.set_page_config(page_title="Stromerzeugung Kennzahlen", layout="wide")
    st.title("Stromerzeugung in Deutschland: Kennzahlen und Bericht")
    st.markdown(
        "Diese App wertet Daten zur Stromerzeugung in Deutschland aus. "
        "Du lädst eine Excel‑Datei hoch, die App berechnet die Kennzahlen und zeigt sie als Diagramme. "
        "Für den Bericht brauchst du einen eigenen **Claude API‑Schlüssel**. Den trägst du links in der "
        "Seitenleiste ein. Der Schlüssel wird nicht gespeichert, die Nutzung wird über dein Konto abgerechnet."
    )

    # API-Schlüssel nur im Passwortfeld der Seitenleiste; er wird nicht gespeichert
    st.sidebar.header("Einstellungen")
    api_key = st.sidebar.text_input("Claude API-Schlüssel", type="password")
    st.sidebar.caption("Der Schlüssel wird nur für diese Sitzung benutzt und nirgends gespeichert.")

    uploaded = st.file_uploader("Excel-Datei hochladen (.xlsx)", type=["xlsx"])
    if uploaded is None:
        st.info("Bitte lade eine Excel-Datei hoch.")
        return

    try:
        df = load_data(uploaded)
        kpis = compute_kpis(df)
    except Exception as error:  # zeigt die Fehlermeldung in der App statt eines Absturzes
        st.error(f"Die Datei konnte nicht gelesen werden: {error}")
        return

    st.subheader("Kennzahlen")
    col1, col2, col3 = st.columns(3)
    col1.metric("Anteil erneuerbar (energiegewichtet)", f"{kpis['renewable_share_energy']:.1%}")
    col2.metric("Gesamterzeugung Mittel", f"{kpis['total_mean_mw']:,.0f} MW")
    col3.metric("Gesamterzeugung Energie", f"{kpis['total_energy_twh']:.1f} TWh")

    st.subheader("Anteil erneuerbarer Energie (Tagesmittel, in %)")
    daily = (kpis["share_daily"].dropna() * 100).rename("Anteil erneuerbar [%]")
    st.line_chart(daily)

    st.subheader("Erzeugte Energie pro Energieträger (GWh)")
    st.bar_chart(kpis["stats"]["Energie [GWh]"].sort_values())

    st.subheader("Tabelle pro Energieträger")
    st.dataframe(kpis["stats"].drop(columns="Zeitpunkt Maximum").round(1))

    st.subheader("Bericht von Claude")
    if not api_key:
        st.warning("Bitte trage links in der Seitenleiste deinen API-Schlüssel ein.")
        return
    if st.button("Bericht erstellen"):
        with st.spinner("Claude schreibt den Bericht ..."):
            try:
                # Der Bericht wird in der Sitzung gespeichert, damit er beim Herunterladen nicht verschwindet
                st.session_state["report"] = {
                    "file": uploaded.name,
                    "text": ask_claude(api_key, build_summary(kpis)),
                }
            except anthropic.APIError as error:
                st.error(f"Die Claude API hat einen Fehler gemeldet: {error}")

    saved = st.session_state.get("report")
    if saved and saved["file"] == uploaded.name:
        st.markdown(saved["text"])
        st.download_button(
            "Bericht als PDF herunterladen",
            data=report_to_pdf(saved["text"]),
            file_name="bericht_stromerzeugung.pdf",
            mime="application/pdf",
        )


if __name__ == "__main__":
    main()
