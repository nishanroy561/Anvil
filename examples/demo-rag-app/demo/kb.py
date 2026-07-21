"""A tiny in-memory knowledge base and a deliberately fake retriever.

The point of the demo is the *tracing*, not the retrieval quality — so this is
keyword overlap over a dozen documents, with no vector store to install.
"""

from __future__ import annotations

import random
from typing import List

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

CORPUS = [
    ("refunds", "Refunds are issued to the original payment method within 5-7 business days."),
    ("refunds", "Orders over $500 require manager approval before a refund is processed."),
    ("shipping", "Standard shipping takes 3-5 business days; express is next-day before 2pm."),
    ("shipping", "We ship to 34 countries. Customs fees are the recipient's responsibility."),
    ("accounts", "Password resets expire after 30 minutes. Request a new link if it lapses."),
    ("accounts", "Two-factor authentication supports TOTP apps and hardware keys."),
    ("billing", "Invoices are generated on the first of each month and emailed to the billing contact."),
    ("billing", "Annual plans are discounted 20% versus paying monthly."),
    ("api", "The API rate limit is 300 requests per minute per key."),
    ("api", "API keys are shown once at creation. Rotate them from the project settings page."),
    ("privacy", "Customer data is retained for 90 days on the free plan and 2 years on paid plans."),
    ("privacy", "We are SOC 2 Type II certified and GDPR compliant."),
]

QUESTIONS = [
    ("How long do refunds take?", "refunds"),
    ("Can I get a refund on a $900 order?", "refunds"),
    ("When will my package arrive?", "shipping"),
    ("Do you ship internationally?", "shipping"),
    ("My password reset link stopped working", "accounts"),
    ("How do I turn on 2FA?", "accounts"),
    ("When am I invoiced?", "billing"),
    ("Is the annual plan cheaper?", "billing"),
    ("What is the API rate limit?", "api"),
    ("I lost my API key, what now?", "api"),
    ("How long do you keep my data?", "privacy"),
    ("Are you GDPR compliant?", "privacy"),
]


class DemoRetriever(BaseRetriever):
    """Keyword-overlap retrieval with a small artificial latency, so the
    waterfall has something with real proportions in it."""

    k: int = 3
    latency_ms: int = 25

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None
    ) -> List[Document]:
        import time

        time.sleep(self.latency_ms / 1000)
        terms = {w.strip("?.,").lower() for w in query.split()}
        scored = []
        for topic, text in CORPUS:
            words = {w.strip(".,").lower() for w in text.split()} | {topic}
            overlap = len(terms & words)
            # A little noise so identical questions don't always retrieve the
            # identical set — makes seeded data look less synthetic.
            scored.append((overlap + random.random() * 0.5, topic, text))
        scored.sort(reverse=True)
        return [
            Document(
                page_content=text,
                metadata={"source": f"kb/{topic}", "score": round(score, 3)},
            )
            for score, topic, text in scored[: self.k]
        ]
