import httpx

from backend.app.domain import PipelineError, ProcessIR


class ContainerRunner:
    def __init__(self, url: str = "http://worker:8081", timeout: float = 7):
        self.url = url.rstrip("/")
        self.timeout = timeout

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                response = await client.get(f"{self.url}/health")
                if response.status_code != 200 or len(response.content) > 4096:
                    return False
                data = response.json()
                return isinstance(data, dict) and data.get("ready") is True
        except (httpx.HTTPError, ValueError, RecursionError):
            return False

    async def run(self, code: str, source_text: str) -> ProcessIR:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                response = await client.post(
                    f"{self.url}/run", json={"code": code, "source_text": source_text}
                )
                response.raise_for_status()
                if len(response.content) > 1024**2:
                    raise PipelineError("OUTPUT_LIMIT", "Превышен лимит ответа worker.")
        except httpx.HTTPError as exc:
            raise PipelineError("RUNNER_UNAVAILABLE", "Изолированный worker недоступен.") from exc
        except UnicodeError as exc:
            raise PipelineError("CODE_POLICY", "Запрос содержит недопустимые символы Unicode.") from exc
        try:
            data = response.json()
        except (ValueError, RecursionError) as exc:
            raise PipelineError("WORKER_PROTOCOL", "Ответ worker не содержит допустимый JSON.") from exc
        if not isinstance(data, dict) or ("error" in data) == ("ir" in data):
            raise PipelineError("WORKER_PROTOCOL", "Некорректная структура ответа worker.")
        if "error" in data:
            error = data["error"]
            if not isinstance(error, dict):
                raise PipelineError("WORKER_PROTOCOL", "Некорректная структура ошибки worker.")
            code = error.get("code", "WORKER_FAILURE")
            message = error.get("message", "Worker отклонил код.")
            element_ids = error.get("element_ids", [])
            if (
                not isinstance(code, str)
                or not code
                or len(code) > 100
                or not isinstance(message, str)
                or len(message) > 2000
                or not isinstance(element_ids, list)
                or len(element_ids) > 100
                or any(not isinstance(item, str) or len(item) > 100 for item in element_ids)
            ):
                raise PipelineError("WORKER_PROTOCOL", "Ошибка worker не соответствует контракту.")
            raise PipelineError(code, message, element_ids)
        try:
            return ProcessIR.model_validate(data["ir"])
        except (KeyError, ValueError) as exc:
            raise PipelineError("WORKER_PROTOCOL", "Ответ worker не соответствует IR.") from exc
