# Checklist para piloto — o que pedir ao ISP

Resumo do que é preciso receber de um provedor (ex.: ISP Direct, Campo
Magro-PR) para rodar um piloto real do VOX-ISP. Organizado por bloco,
cada um independente — dá para começar mesmo sem ter tudo.

## 1. ERP (sistema de gestão) — obrigatório

- **Qual ERP usam?** Hoje só a **Hubsoft** tem conector real pronto
  (`HubsoftConnector`). Se for outro (IXC, SGP, Voalle, MK Solutions),
  precisa da doc da API deles antes de construir o conector.
- Se for Hubsoft: pedir ao suporte deles (`suporte@hubsoft.com.br`), com
  autorização do gestor do provedor:
  - URL do Hubsoft do provedor (`https://<provedor>.hubsoft.com.br`)
  - `client_id` / `client_secret` (credenciais de integração)
  - `username` / `password` de um usuário com permissão de API
- **Sem sandbox público** — a Hubsoft só libera essas credenciais para
  cliente pagante. Não dá para testar antes disso.

## 2. ACS (gerência de roteador/CPE) — opcional

Sem isso, diagnóstico óptico e reboot remoto do roteador não funcionam
(o resto do sistema funciona normalmente).

- **Usam GenieACS?** Já tem adapter pronto — só a URL (`GENIEACS_BASE_URL`)
  e, se tiver proxy com autenticação, usuário/senha.
- Outro ACS (Aprecomm etc.): não implementado ainda.
- **Pergunta técnica importante**: qual(is) modelo(s) de ONT/roteador o
  provedor usa? A potência óptica (RX power) não tem um padrão universal
  — cada fabricante usa um caminho diferente. Já cobrimos Huawei, ZTE e
  Nokia; se for outro fabricante, precisa mapear o parâmetro certo.

## 3. NMS (monitoramento de rede) — opcional

Sem isso, a detecção de "queda em massa" (§4.5 da spec — o item de maior
ROI) não funciona; cada chamada vira diagnóstico individual.

- **Usam Zabbix?** Já tem adapter pronto — URL + usuário/senha de API.
- Combinar a convenção: cada host de OLT no Zabbix precisa de uma **tag**
  (`olt_id`, chave configurável) com o mesmo ID que o ERP usa para aquela
  OLT — é assim que o sistema correlaciona os dois.

## 4. Telefonia — obrigatório para testar a chamada de verdade

- Um **ramal ou rota de teste isolada** no Asterisk do provedor (não em
  produção), onde dá para configurar uma extensão `AudioSocket()`
  apontando para o nosso servidor.
- Alternativa mais segura para o primeiro teste: nos dar acesso a um
  Asterisk de homologação deles (ou usarmos um nosso) e só depois migrar
  para o Asterisk real de produção.
- Confirmar: codec usado (G.711a é o padrão no Brasil — já é o que o
  sistema assume).

## 5. Contas de voz (podemos criar nós, ou o provedor já ter)

- **Deepgram** (reconhecimento de fala) — conta + API key
- **ElevenLabs** (síntese de voz) — conta + API key + escolher/clonar uma
  voz (não existe voz genérica — precisa ser uma voz específica da conta)
- **Anthropic (Claude)** — já temos chave; pode usar a nossa ou a do
  provedor

## 6. Dados de teste

- 2–3 CPFs de assinantes reais de teste (ou um ambiente de homologação
  com dados fictícios, se o provedor tiver)
- Cenários pra validar: um assinante com fatura em aberto (FIN-02), um
  relatando "sem internet" (NET-01) — idealmente incluindo um cenário de
  incidente em massa se for possível simular, um com OS aberta (OPS-01)

## 7. Compliance — antes de qualquer chamada com cliente real

- Aviso obrigatório de gravação e de que é atendimento automatizado
  (Decreto 11.034/SAC)
- Confirmar com o jurídico do provedor: retenção de dados (LGPD),
  gravação de CPF (nunca em claro nos logs — já implementado)
- **Isso não é algo que este projeto resolve sozinho** — precisa de
  parecer jurídico formal antes de qualquer go-live (ver spec §6)

---

## O que já está pronto, não precisa esperar nada disso

FSM completa, todos os 7 intents (FIN-02/03, NET-01/04, OPS-01/02/03)
com tool-calling e confirmação de ação destrutiva, persistência real
(Postgres + migrações Alembic), conector Hubsoft real, adapters
GenieACS/Zabbix reais, ASR (Deepgram)/TTS (ElevenLabs) reais, ponte de
telefonia real (AudioSocket, testada contra Asterisk de verdade via
Docker) e o voice runtime que liga tudo — 203 testes automatizados.

## O que **não** dá para testar sem o piloto

- Hubsoft real (sem sandbox — únicos testes hoje são contra a doc pública)
- Qualidade de voz e latência ponta a ponta reais
- Volume real de chamadas simultâneas
- As 4 pontas de voz (Asterisk + Deepgram + Claude + ElevenLabs) juntas
  ao mesmo tempo — cada uma foi validada isoladamente, nunca as 4 juntas

Ver `README.md` (seção "Status e próximos passos") e os docs individuais
em `docs/connectors/`, `docs/voice/` e `docs/telephony/` para detalhes
técnicos completos de cada peça.
