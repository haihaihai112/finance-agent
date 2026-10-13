"""Public-policy retrieval with effective filtering and conservative byte token bound."""

from datetime import date
from finance_agents.contracts.common import AuthContext


class EvidenceRetriever:
    def __init__(self, store, jurisdiction="中国大陆", on_date=None):
        self.store, self.jurisdiction, self.on_date = store, jurisdiction, on_date

    async def search(self, query: str, auth: AuthContext, token_budget: int = 6000):
        if token_budget < 1:
            return []
        refs = []
        remaining = token_budget
        # UTF-8 bytes provide a conservative token bound, not the model's exact tokenizer.
        for row in self.store.search(query, self.jurisdiction, self.on_date or date.today()):
            ref = self.store.evidence(row)
            overhead = len(ref.model_dump_json(exclude={"quote"}).encode("utf-8"))
            if overhead >= remaining:
                continue
            quote = ""
            for char in ref.quote or "":
                cost = len(char.encode("utf-8"))
                if len(quote.encode("utf-8")) + cost > remaining - overhead:
                    break
                quote += char
            if not quote:
                continue
            ref = ref.model_copy(update={"quote": quote})
            remaining -= overhead + len(quote.encode("utf-8"))
            refs.append(ref)
        return refs
