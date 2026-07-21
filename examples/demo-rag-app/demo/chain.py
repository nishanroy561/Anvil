"""The RAG chain: retrieve -> prompt -> LLM.

The LLM is **mocked by default** so the demo and its tests run keyless
(CLAUDE.md). Set `DEMO_LLM=real` with a provider key to swap in a live model —
nothing else in the demo changes, which is the point.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any, List, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from .kb import DemoRetriever

# Names that exist in the worker's price table, so seeded traces get real
# cost numbers instead of nulls (apps/worker/anvil_worker/pricing.py).
DEMO_MODELS = ["claude-sonnet-5", "claude-haiku-4-5", "gpt-4o-mini"]

PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", "Answer the question using only the context below.\n\nContext:\n{context}"),
        ("human", "{question}"),
    ]
)


class DemoLLMError(RuntimeError):
    """Simulated provider failure — seeds the error traces the dashboard and
    the failure inbox (Phase 9) need to have something to show."""


class MockChatModel(BaseChatModel):
    """A fake chat model that behaves enough like a real one to be useful:
    it reports token usage, takes plausible time, and can fail."""

    model: str = "claude-sonnet-5"
    latency_ms: int = 350
    fail: bool = False
    slow: bool = False

    @property
    def _llm_type(self) -> str:
        return "demo-mock"

    @property
    def _identifying_params(self) -> dict:
        # Surfaces as `invocation_params` on the callback, which is where
        # AnvilCallbackHandler reads the model name from.
        return {"model": self.model, "temperature": 0.2}

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        delay = self.latency_ms * (6 if self.slow else 1)
        time.sleep(delay / 1000)
        if self.fail:
            raise DemoLLMError(f"{self.model}: upstream provider returned 529 overloaded")

        question = messages[-1].content if messages else ""
        prompt_chars = sum(len(str(m.content)) for m in messages)
        answer = _canned_answer(str(question))
        message = AIMessage(
            content=answer,
            usage_metadata={
                # ~4 chars per token is close enough for demo cost figures.
                "input_tokens": max(1, prompt_chars // 4),
                "output_tokens": max(1, len(answer) // 4),
                "total_tokens": max(2, (prompt_chars + len(answer)) // 4),
            },
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _canned_answer(question: str) -> str:
    lowered = question.lower()
    if "refund" in lowered:
        return "Refunds land on your original payment method in 5-7 business days. Orders over $500 need manager approval first."
    if "ship" in lowered or "package" in lowered:
        return "Standard shipping is 3-5 business days. Express orders placed before 2pm arrive next day."
    if "password" in lowered or "2fa" in lowered:
        return "Reset links expire after 30 minutes - request a fresh one. 2FA supports TOTP apps and hardware keys."
    if "invoice" in lowered or "plan" in lowered or "billing" in lowered:
        return "Invoices go out on the 1st to your billing contact. Annual plans save 20% over monthly."
    if "api" in lowered or "key" in lowered:
        return "The limit is 300 requests per minute per key. Keys are shown once - rotate them in project settings."
    if "data" in lowered or "gdpr" in lowered:
        return "Free plans retain data for 90 days, paid for 2 years. We are SOC 2 Type II certified and GDPR compliant."
    return "I could not find that in the knowledge base."


def build_model(
    model: Optional[str] = None, fail: bool = False, slow: bool = False
) -> BaseChatModel:
    name = model or random.choice(DEMO_MODELS)
    if os.getenv("DEMO_LLM", "mock").lower() == "real":
        try:
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(model=name if name.startswith("claude") else "claude-sonnet-5")
        except ImportError:
            print("DEMO_LLM=real but langchain-anthropic is not installed; using the mock")
    return MockChatModel(model=name, fail=fail, slow=slow)


def format_docs(docs: List[Document]) -> str:
    return "\n\n".join(f"[{d.metadata['source']}] {d.page_content}" for d in docs)


def build_chain(model: Optional[BaseChatModel] = None, k: int = 3):
    """The whole app in one runnable. Instrumentation is *not* in here — the
    caller attaches AnvilCallbackHandler, which is the one-line story."""
    retriever = DemoRetriever(k=k)
    llm = model or build_model()

    def run(question: str, config=None) -> dict:
        docs = retriever.invoke(question, config=config)
        messages = PROMPT.invoke(
            {"context": format_docs(docs), "question": question}, config=config
        )
        answer = llm.invoke(messages, config=config)
        return {
            "answer": answer.content,
            "sources": [d.metadata["source"] for d in docs],
        }

    return RunnableLambda(run, name="rag_pipeline")
