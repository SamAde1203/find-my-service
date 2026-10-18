from __future__ import annotations

from find_my_service.ingest import extract_chunks, should_visit
from find_my_service.usage import UsageTracker

HTML = """
<html><body><nav><a href="/x">Menu</a></nav>
<main>
  <h1>Council Tax bands and charges</h1>
  <p>Your band depends on the value of your home on 1 April 1991.</p>
  <table><tr><th>Band</th><th>Annual charge</th></tr>
         <tr><td>D</td><td>£2,280.18</td></tr></table>
  <div class="lgd-region--sidebar-first">
    <h2>Publication navigation</h2><p>Chapter links here.</p>
  </div>
  <h2>Other ways to pay</h2>
  <ul><li>by phone using our automated line</li><li>at any Post Office</li></ul>
  <p>This file may not be suitable for users of assistive technology.
     Request an accessible format.</p>
</main></body></html>
"""


def test_extract_splits_sections_and_labels_table_rows() -> None:
    chunks = extract_chunks("https://council.example/ctax", HTML)
    assert [c.heading for c in chunks] == [None, "Other ways to pay"]
    assert "Band: D, Annual charge: £2,280.18." in chunks[0].text
    assert "Publication navigation" not in " ".join(c.text for c in chunks)
    assert chunks[1].text == "By phone using our automated line. At any Post Office."
    assert all("assistive technology" not in c.text for c in chunks)
    assert chunks[1].title == "Council Tax bands and charges: Other ways to pay"


def test_crawler_stays_on_content_pages() -> None:
    host = "council.example"
    assert should_visit("https://council.example/council-tax", host)
    assert not should_visit("https://other.example/council-tax", host)
    assert not should_visit("https://council.example/user/login", host)
    assert not should_visit("https://council.example/search?s=bins", host)
    assert not should_visit("https://council.example/sites/default/files/a.pdf", host)


def test_usage_tracks_cost_and_budget() -> None:
    usage = UsageTracker(price_input_per_mtok=1.0, price_output_per_mtok=5.0, daily_budget_usd=0.01)
    assert usage.can_spend()
    cost = usage.record(input_tokens=2000, output_tokens=1000)
    assert cost == 0.007
    assert usage.can_spend()
    usage.record(input_tokens=2000, output_tokens=1000)
    assert not usage.can_spend()
    assert usage.snapshot()["calls"] == 2
