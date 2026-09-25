"""Every model call (including existing cycle nodes) consumes the attempt budget."""

from dataclasses import replace

from pydantic import BaseModel

from thoth.application.services.model_wait import await_current_model
from thoth.domain.model import ModelRequest, ModelResult
from thoth.domain.model_dispatch import ModelCallContext
from thoth.domain.research_execution import ResearchUsageObserver, model_call, research_work
from thoth.ports.model import ModelExecutionHold, ModelPort, ModelResolverPort
from thoth.ports.model_transport import ModelControlPort


class ResearchModel:
    def __init__(self, delegate: ModelPort) -> None:
        self.delegate = delegate

    async def structured[T: BaseModel](self, request: ModelRequest[T]) -> ModelResult[T]:
        work = research_work.get()
        if work is not None:
            request = replace(request, model_settings=work.model_settings)
            if isinstance(work.boundary, ResearchUsageObserver):
                usage = work.boundary.usage_observation()
                request = replace(
                    request,
                    context_pack=request.context_pack.model_copy(
                        update={
                            "research_context": {
                                **request.context_pack.research_context,
                                "usage_observation": usage,
                                "usage_guidance": "Use observed token usage to focus next steps, "
                                "avoid redundant calls, and decide when evidence is sufficient. "
                                "No fixed local research time/call cutoff is enforced. "
                                "Unknown account quota or cached tokens are not zero; do not infer "
                                "remaining weekly quota from this thread's tokens or bytes.",
                            },
                        }
                    ),
                )
            if (
                not isinstance(self.delegate, ModelControlPort)
                or self.delegate.control_capability.output_control == "UNVERIFIED"
            ):
                raise ModelExecutionHold("MODEL_TRANSPORT_CONTROLS_UNREGISTERED")
        token = (
            None
            if work is None
            else model_call.set(ModelCallContext(work.boundary.new_model_call()))
        )

        async def dispatch() -> ModelResult[T]:
            if work is not None and work.observe_context is not None:
                work.observe_context(request.context_pack)
            return await self.delegate.structured(request)

        try:
            result = (
                await dispatch()
                if work is None
                else await await_current_model(
                    dispatch,
                    work.boundary.check,
                    work.boundary.call_timeout(),
                )
            )
        except TimeoutError as exc:
            if work is not None:
                # Distinguish an exhausted persisted attempt from an independently
                # shortened call deadline. Never reset allowance on this path.
                work.boundary.call_timeout()
            raise ModelExecutionHold("MODEL_CALL_TIME_BUDGET_EXHAUSTED") from exc
        finally:
            if token is not None:
                model_call.reset(token)
        if work is not None:
            work.boundary.check()
        return result


class ResearchModels:
    def __init__(self, delegate: ModelResolverPort) -> None:
        self.delegate = delegate

    def resolve(self, *, provider: str, model: str | None = None) -> ModelPort:
        return ResearchModel(self.delegate.resolve(provider=provider, model=model))
