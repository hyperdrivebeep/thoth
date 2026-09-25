"""Memory review consumes the same prepared transport and research allowance."""

import asyncio

from thoth.domain.research_execution import research_work, reserve_model_dispatch
from thoth.ports.model import ModelTransportCancelled, ModelTransportHold
from thoth.ports.model_transport import BoundedModelExecutorPort


async def bounded_review(
    executor: BoundedModelExecutorPort, prompt: str, schema: dict[str, object]
) -> str:
    work = research_work.get()
    prepared = executor.prepare(
        prompt,
        schema,
        output_tokens=512,
        timeout_seconds=300 if work is None else work.boundary.call_timeout(),
        model_settings=None if work is None else work.model_settings,
    )
    dispatch = reserve_model_dispatch(
        prepared.payload, prepared.output_tokens_reserved, prepared.capability
    )
    try:
        reply = await asyncio.wait_for(executor.dispatch(prepared), prepared.timeout_seconds)
    except (ModelTransportHold, ModelTransportCancelled) as exc:
        if work is not None:
            work.boundary.record_usage(
                dispatch,
                exc.observation.received_bytes,
                None,
                None,
                "UNKNOWN",
                exc.observation.response_id,
                observation=exc.observation,
            )
        if isinstance(exc, ModelTransportCancelled):
            raise asyncio.CancelledError() from exc
        raise
    if work is not None:
        work.boundary.record_usage(
            dispatch,
            reply.received_bytes,
            reply.input_tokens,
            reply.output_tokens,
            reply.remote_stop,
            reply.response_id,
            observation=reply.observation,
        )
        work.boundary.check()
    return reply.text
