"""LangChain integration — run trees become Anvil span trees.

Everything here runs on langchain's fake models: the suite is keyless and makes
no network calls (CLAUDE.md).
"""

from typing import List
from uuid import uuid4

import pytest
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool

import anvil_sdk as anvil
from anvil_sdk.langchain import AnvilCallbackHandler


class StubRetriever(BaseRetriever):
    def _get_relevant_documents(self, query: str, *, run_manager=None) -> List[Document]:
        return [Document(page_content=f"about {query}", metadata={"source": "kb-1"})]


def queued(client) -> list:
    return list(client._queue)


def test_chain_run_tree_becomes_a_span_tree(client, validate_batch):
    model = FakeListChatModel(responses=["Paris is the capital."])
    prompt = ChatPromptTemplate.from_template("Answer: {question}")
    chain = prompt | model

    handler = AnvilCallbackHandler(trace_name="qa")
    chain.invoke({"question": "capital of France?"}, config={"callbacks": [handler]})

    batch = queued(client)
    assert len(batch) == 1, "one trace per top-level run"
    validate_batch(batch)

    trace = batch[0]
    assert trace["name"] == "qa"
    assert trace["status"] == "ok"

    types = [s["type"] for s in trace["spans"]]
    assert "llm" in types
    llm_span = next(s for s in trace["spans"] if s["type"] == "llm")
    assert "Paris" in str(llm_span["output"])

    # Every non-root span points at a span that exists in this trace — the
    # property the waterfall depends on.
    ids = {s["id"] for s in trace["spans"]}
    for span in trace["spans"]:
        assert span["parent_span_id"] is None or span["parent_span_id"] in ids


def test_retrieval_and_tool_spans_nest_under_the_chain(client, validate_batch):
    retriever = StubRetriever()

    @tool
    def lookup(term: str) -> str:
        """Look a term up."""
        return f"definition of {term}"

    model = FakeListChatModel(responses=["done"])

    def pipeline(question: str, config=None) -> str:
        docs = retriever.invoke(question, config=config)
        lookup.invoke({"term": question}, config=config)
        model.invoke(docs[0].page_content, config=config)
        return "done"

    handler = AnvilCallbackHandler(trace_name="rag")
    RunnableLambda(pipeline).invoke("gravity", config={"callbacks": [handler]})

    batch = queued(client)
    validate_batch(batch)
    spans = batch[0]["spans"]
    by_type = {s["type"] for s in spans}
    assert {"retrieval", "tool", "llm"} <= by_type

    retrieval = next(s for s in spans if s["type"] == "retrieval")
    assert retrieval["output"][0]["text"] == "about gravity"
    assert retrieval["output"][0]["metadata"]["source"] == "kb-1"

    tool_span = next(s for s in spans if s["type"] == "tool")
    assert "definition of gravity" in str(tool_span["output"])

    # The lambda is the root; the three leaf spans hang off it.
    root = next(s for s in spans if s["parent_span_id"] is None)
    assert root["type"] == "chain"
    assert all(s["parent_span_id"] == root["id"] for s in spans if s is not root)


def test_chain_error_marks_trace_and_span(client, validate_batch):
    def explode(_: str) -> str:
        raise RuntimeError("retriever offline")

    handler = AnvilCallbackHandler(trace_name="broken")
    with pytest.raises(RuntimeError):
        RunnableLambda(explode).invoke("x", config={"callbacks": [handler]})

    batch = queued(client)
    validate_batch(batch)
    assert batch[0]["status"] == "error"
    assert "retriever offline" in batch[0]["metadata"]["error"]
    assert batch[0]["spans"][0]["status"] == "error"


def test_token_usage_from_llm_output(client, validate_batch):
    """Tokens drive cost, computed by the worker at persist time — the handler's
    job is to carry whatever the provider reported."""
    handler = AnvilCallbackHandler(trace_name="usage")
    run_id = uuid4()

    handler.on_llm_start(
        {"name": "ChatAnthropic"},
        ["hello"],
        run_id=run_id,
        invocation_params={"model": "claude-sonnet-4-5", "temperature": 0.2},
    )
    handler.on_llm_end(
        LLMResult(
            generations=[[ChatGeneration(message=FakeListChatModel(responses=["hi"]).invoke("x"))]],
            llm_output={"token_usage": {"prompt_tokens": 120, "completion_tokens": 30}},
        ),
        run_id=run_id,
    )

    batch = queued(client)
    validate_batch(batch)
    span = batch[0]["spans"][0]
    assert (span["tokens_in"], span["tokens_out"]) == (120, 30)
    assert span["attributes"]["model"] == "claude-sonnet-4-5"
    assert span["attributes"]["temperature"] == 0.2
    assert batch[0]["model"] == "claude-sonnet-4-5", "trace model drives cost roll-up"


def test_usage_metadata_fallback(client, validate_batch):
    """Newer langchain reports usage per-generation instead of in llm_output."""

    class Msg:
        usage_metadata = {"input_tokens": 7, "output_tokens": 2}
        content = "hi"

    class Gen:
        message = Msg()
        text = "hi"

    class Result:
        llm_output = {}
        generations = [[Gen()]]

    handler = AnvilCallbackHandler()
    run_id = uuid4()
    handler.on_llm_start({"name": "m"}, ["p"], run_id=run_id)
    handler.on_llm_end(Result(), run_id=run_id)

    span = queued(client)[0]["spans"][0]
    assert (span["tokens_in"], span["tokens_out"]) == (7, 2)


def test_handler_exposes_the_trace_id_for_feedback(client):
    model = FakeListChatModel(responses=["ok"])
    handler = AnvilCallbackHandler(trace_name="qa", session_id="sess-1", user_ref="u-9")
    model.invoke("hi", config={"callbacks": [handler]})

    trace = queued(client)[0]
    assert handler.trace.id == trace["id"], "round-trip this id to the frontend"
    assert trace["session_id"] == "sess-1"
    assert trace["user_ref"] == "u-9"


def test_reusing_a_handler_starts_a_fresh_trace(client, validate_batch):
    model = FakeListChatModel(responses=["a", "b"])
    handler = AnvilCallbackHandler(trace_name="qa")

    model.invoke("one", config={"callbacks": [handler]})
    model.invoke("two", config={"callbacks": [handler]})

    batch = queued(client)
    validate_batch(batch)
    assert len(batch) == 2
    assert batch[0]["id"] != batch[1]["id"]


def test_callback_bugs_do_not_break_the_chain(client):
    """@_safe is load-bearing: a handler that blows up must cost a span, not
    the customer's request (ADR-0013)."""
    handler = AnvilCallbackHandler()
    model = FakeListChatModel(responses=["still works"])

    handler._open = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sdk bug"))

    result = model.invoke("hi", config={"callbacks": [handler]})
    assert result.content == "still works"


def test_a_dead_client_does_not_break_the_chain(client):
    anvil.set_client(None)
    handler = AnvilCallbackHandler()
    model = FakeListChatModel(responses=["fine"])
    assert model.invoke("hi", config={"callbacks": [handler]}).content == "fine"
