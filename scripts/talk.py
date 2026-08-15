"""Cliente de terminal para a API demo (`voxisp.main`) — simula uma ligação
via texto, turno a turno, em vez de montar `curl` na mão.

Uso (com `make dev` rodando em outro terminal, porta 8000 por padrão):

    .venv/bin/python scripts/talk.py
    .venv/bin/python scripts/talk.py --url http://localhost:8000

Fluxo: pede o CPF primeiro (regra de identificação da FSM, spec §4.1),
depois entra em loop livre de falas. Comandos especiais:
  :sair       encerra o script (não encerra a ligação no servidor)
  :fim        chama POST /calls/{id}/end (equivalente a desligar sem transbordo)

Cada resposta imprime o texto do assistente e, quando relevante, o motivo
de transbordo/protocolo — os mesmos campos que um voice runtime real
receberia de `TurnResponse` (ver voxisp/main.py).
"""
from __future__ import annotations

import argparse
import sys

import httpx


def _print_turn(label: str, data: dict) -> None:
    print(f"\n🤖 {data.get('text', '')}")
    if data.get("protocol_number"):
        print(f"   [protocolo: {data['protocol_number']}]")
    if data.get("escalate"):
        print(f"   [TRANSBORDO — motivo: {data.get('escalation_reason')}]")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL da API demo")
    args = parser.parse_args()

    with httpx.Client(base_url=args.url, timeout=10.0) as client:
        try:
            health = client.get("/health").json()
        except httpx.ConnectError:
            print(f"Não consegui conectar em {args.url} — a API demo está rodando? (`make dev`)")
            sys.exit(1)
        print(
            f"Conectado — isp_connector={health['isp_connector']} "
            f"llm_provider={health['llm_provider']} "
            f"tool_calling={health['tool_calling_enabled']} "
            f"persistence={health['persistence_enabled']}"
        )

        start = client.post("/calls").json()
        # POST /calls devolve o texto prefixado com "[call_id] " (ver
        # voxisp/main.py:start_call) — não há campo dedicado no schema.
        prefix, _, greeting_text = start["text"].partition("] ")
        call_id = prefix.lstrip("[")
        print(f"\n📞 Ligação iniciada (call_id={call_id})")
        _print_turn("assistant", {**start, "text": greeting_text})

        cpf = input("\n🧑 CPF: ").strip()
        result = client.post(f"/calls/{call_id}/identify", json={"cpf": cpf}).json()
        _print_turn("assistant", result)
        if result.get("escalate"):
            print("\nLigação transbordada na identificação. Encerrando.")
            return

        while True:
            utterance = input("\n🧑 Você: ").strip()
            if not utterance:
                continue
            if utterance == ":sair":
                break
            if utterance == ":fim":
                client.post(f"/calls/{call_id}/end")
                print("\nLigação encerrada (sem transbordo).")
                break

            result = client.post(f"/calls/{call_id}/utterance", json={"text": utterance}).json()
            _print_turn("assistant", result)
            if result.get("escalate"):
                print("\nLigação transbordada. Encerrando o script.")
                break


if __name__ == "__main__":
    main()
