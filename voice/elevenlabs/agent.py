"""The ElevenLabs agent as code, and the few REST calls this layer makes.

The agent is a shell: ears, turn-taking and a voice, with its model set to our custom-LLM endpoint
(voice/elevenlabs/brain.py). Its own prompt only carries the call id, so the endpoint knows which
conversation a request belongs to even if the extra body is missing. The built-in end-call tool is
the only system tool it has; it runs only when the endpoint emits it. Voice and voice model come
from `realtime.elevenlabs` in config/models.yaml.

Endpoints (SOURCE: https://elevenlabs.io/docs/eleven-agents/api-reference): POST
/v1/convai/agents/create, DELETE /v1/convai/agents/{id}, GET /v1/convai/conversations/{id}.
"""

import httpx

BASE_URL = "https://api.elevenlabs.io"
TEST_AGENT_NAME = "Summit Air rig test (temporary, deleted after the run)"
# Our endpoint has the caller's audio as 16 kHz PCM in and out, as the rig streams it.
AUDIO_FORMAT = "pcm_16000"


def agent_body(llm_url: str, model_id: str, cfg: dict, name: str = TEST_AGENT_NAME) -> dict:
    return {
        "name": name,
        "conversation_config": {
            "agent": {
                "first_message": "",
                "language": "en",
                "prompt": {
                    "prompt": "Summit Air call. summit_call_id={{summit_call_id}}",
                    "llm": "custom-llm",
                    "custom_llm": {"url": llm_url, "model_id": model_id},
                    "built_in_tools": {
                        "end_call": {
                            "name": "end_call",
                            "description": "Ends the call. Used only when the server says so.",
                            "params": {"system_tool_type": "end_call"},
                        }
                    },
                },
            },
            "tts": {
                "model_id": cfg["tts_model"],
                "voice_id": cfg["voice_id"],
                "agent_output_audio_format": AUDIO_FORMAT,
            },
            "asr": {"user_input_audio_format": AUDIO_FORMAT},
            # A cost cap, as on Retell (voice/retell/README.md).
            "conversation": {"max_duration_seconds": 600},
        },
        "platform_settings": {
            "overrides": {
                "conversation_config_override": {"agent": {"first_message": True}},
                "custom_llm_extra_body": True,
            },
        },
    }


class ElevenLabs:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(
            base_url=BASE_URL, headers={"xi-api-key": api_key}, timeout=30
        )

    async def _json(self, method: str, path: str, **kwargs) -> dict:
        response = await self.client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"ElevenLabs {method} {path}: {response.status_code} "
                               f"{response.text[:300]}")
        return response.json() if response.content else {}

    async def create_agent(self, body: dict) -> str:
        return (await self._json("POST", "/v1/convai/agents/create", json=body))["agent_id"]

    async def delete_agent(self, agent_id: str) -> None:
        await self._json("DELETE", f"/v1/convai/agents/{agent_id}")

    async def conversation(self, conversation_id: str) -> dict:
        return await self._json("GET", f"/v1/convai/conversations/{conversation_id}")

    async def aclose(self) -> None:
        await self.client.aclose()
