from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_GA4_BASE = "https://analyticsdata.googleapis.com/v1beta"


class GA4Client:
    """
    Client for GA4 Data API (Analytics Data API v1beta).

    Uses an OAuth access_token obtained via the connector OAuth flow.
    For service-account auth, pass the token directly.
    """

    def __init__(self, access_token: str) -> None:
        self._token = access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def run_report(
        self,
        property_id: str,
        start_date: str = "7daysAgo",
        end_date: str = "today",
        metrics: list[str] | None = None,
        dimensions: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Run a GA4 report and return the raw response.

        Default metrics: sessions, totalUsers, screenPageViews, bounceRate
        Default dimensions: date
        """
        if metrics is None:
            metrics = ["sessions", "totalUsers", "screenPageViews", "bounceRate"]
        if dimensions is None:
            dimensions = ["date"]

        body = {
            "dateRanges": [{"startDate": start_date, "endDate": end_date}],
            "metrics": [{"name": m} for m in metrics],
            "dimensions": [{"name": d} for d in dimensions],
            "orderBys": [{"dimension": {"dimensionName": dimensions[0]}}] if dimensions else [],
        }

        # Strip "properties/" prefix if already included
        prop = property_id.lstrip("properties/") if property_id.startswith("properties/") else property_id
        # Re-add standardised prefix
        if not prop.startswith("properties/"):
            prop = f"properties/{prop}"

        url = f"{_GA4_BASE}/{prop}:runReport"
        resp = httpx.post(
            url,
            headers=self._headers(),
            content=json.dumps(body).encode(),
            timeout=20.0,
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def parse_report(report: dict[str, Any]) -> dict[str, Any]:
        """
        Convert raw GA4 report response into a simplified summary dict.

        Returns:
          {
            "period_rows": [{"date": "20260419", "sessions": 123, ...}],
            "totals": {"sessions": 456, "totalUsers": 321, ...}
          }
        """
        dimension_headers = [h["name"] for h in report.get("dimensionHeaders", [])]
        metric_headers = [h["name"] for h in report.get("metricHeaders", [])]
        rows = []

        for row in report.get("rows", []):
            dim_values = [v["value"] for v in row.get("dimensionValues", [])]
            met_values = [v["value"] for v in row.get("metricValues", [])]
            entry: dict[str, Any] = dict(zip(dimension_headers, dim_values))
            for k, v in zip(metric_headers, met_values):
                try:
                    entry[k] = float(v) if "." in v else int(v)
                except ValueError:
                    entry[k] = v
            rows.append(entry)

        # Aggregate totals
        totals: dict[str, float] = {}
        for row in rows:
            for key in metric_headers:
                val = row.get(key, 0)
                totals[key] = totals.get(key, 0.0) + (float(val) if isinstance(val, (int, float)) else 0.0)

        return {"period_rows": rows, "totals": totals}
