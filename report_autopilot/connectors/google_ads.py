"""
connectors/google_ads.py
-------------------------
NOT a working live connector -- this shows exactly what one would need
so a future implementer (or you, once you have real API access) isn't
starting from zero. Calling fetch() raises NotImplementedError on
purpose, rather than silently returning fake data.

To make this real, you would need:
1. A Google Ads API developer token (requires an approved Google Ads
   manager account -- this alone can take days for approval)
2. OAuth2 credentials (client ID/secret) + a refresh token per client
   account you're pulling data for
3. The `google-ads` Python client library
4. A GAQL (Google Ads Query Language) query for the metrics this
   pipeline needs, roughly:

    SELECT
      segments.date, campaign.name, metrics.impressions,
      metrics.clicks, metrics.cost_micros, metrics.conversions,
      metrics.conversions_value
    FROM campaign
    WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'

5. Conversion of cost_micros (Google's unit) to real currency
   (divide by 1,000,000), and mapping the query response into the
   REQUIRED_COLUMNS schema from connectors/base.py

Realistic estimate: 1-2 days of focused work per platform, assuming
API access is already approved -- most of that time goes to OAuth
setup and handling the account-approval wait, not the code itself.
"""

import pandas as pd
from report_autopilot.connectors.base import DataConnector, ConnectorError


class GoogleAdsConnector(DataConnector):
    def __init__(self, developer_token: str = None, client_id: str = None,
                 client_secret: str = None, refresh_token: str = None,
                 customer_id: str = None):
        self.developer_token = developer_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.customer_id = customer_id

    def fetch(self, start_date: str, end_date: str) -> pd.DataFrame:
        raise NotImplementedError(
            "GoogleAdsConnector is a documented interface stub, not a working "
            "integration -- see the module docstring for exactly what's needed "
            "to implement it for real (Google Ads API access + OAuth setup). "
            "Use the CSV export path (--data + --platform google_ads) in the "
            "meantime, which works today."
        )
