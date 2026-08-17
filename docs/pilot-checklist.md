# Piloto VOX-ISP — o que precisamos alinhar para começar

Provedores de 10 a 200 mil assinantes concentram 60–75% das ligações em
um punhado de pedidos repetitivos: 2ª via de boleto, "estou sem
internet", status de OS, desbloqueio de confiança. O VOX-ISP resolve
essas sozinho — meta de 55–65% de contenção em 12 meses — e entrega as
demais para o atendente humano **já com o contexto da ligação
carregado**, cortando o tempo médio de atendimento em até 25%.

Este é o roteiro do que precisamos alinhar com o provedor para colocar um
piloto no ar. Está organizado em blocos independentes — dá para começar
sem ter todos prontos, e nada aqui compromete a operação atual: o piloto
roda em paralelo, sem tirar nenhuma ligação do fluxo existente até
estarmos confiantes nos números.

> **Status já levantado com a ISP Direct (Campo Magro-PR):** usam
> **Hubsoft** como ERP/CRM — já temos integração real pronta, é só pedir
> as credenciais. O ACS/monitoramento hoje é o **OLT Cloud** (NService —
> reúne TR-069 e SNMP no mesmo produto); não achamos API pública
> documentada, então essa parte pede uma conversa direta com o
> fornecedor deles antes de integrar. Pode ser substituído no futuro
> pelo **VCS da Aprecomm**, que também não tem doc pública — mesmo
> caminho: contato direto com o fornecedor primeiro.

## 1. Sistema de gestão (ERP) — essencial

- **ISP Direct: já resolvido.** Usam Hubsoft, e já temos integração real
  pronta (`HubsoftConnector`) — só falta pedir as credenciais.
- Para pedir: a equipe do provedor solicita ao suporte da Hubsoft
  (`suporte@hubsoft.com.br`), com autorização do gestor:
  - URL do Hubsoft do provedor
  - Credenciais de integração (`client_id`/`client_secret`) e um usuário
    com permissão de API
- A Hubsoft não libera essas credenciais para ambiente de teste — só
  para cliente pagante, então esse passo já usa o ambiente real deles
  (com cuidado: só leitura/ações que o próprio piloto vai executar).
- Para um provedor futuro com outro ERP (IXC, SGP, Voalle, MK Solutions),
  construímos a integração antes de começar — rápido desde que tenhamos a
  documentação da API deles.

## 2. Gerência de roteador e monitoramento de rede (ACS/NMS)

Sem isso o sistema funciona normalmente, só não faz diagnóstico óptico,
reboot remoto do roteador, nem a detecção de "queda em massa" — o bloco
que mais economiza chamadas em dia de rompimento (uma OLT caindo vira uma
única ligação identificando o problema, em vez de dezenas de OS
individuais).

- **ISP Direct usa OLT Cloud** (fabricante NService) para as duas coisas
  no mesmo produto. Não é uma das integrações que já temos prontas
  (GenieACS/Zabbix) e não achamos documentação de API pública — o
  próximo passo é pedir para o time deles (`config@oltcloud.co`) a
  documentação de integração, ou confirmar se expõem SNMP/TR-069 de um
  jeito que dê para consumir diretamente.
- Se no futuro migrarem para o **VCS da Aprecomm**: mesma situação, sem
  doc pública — também exige contato direto com o fornecedor antes de
  integrar.
- Se qualquer um dos dois usar **GenieACS** ou **Zabbix** por baixo (ou
  vier a usar), já temos adapter pronto — só precisamos da URL de acesso.
- Vale perguntar qual(is) modelo(s) de ONT/roteador estão no parque — a
  leitura de sinal óptico varia por fabricante (já cobrimos Huawei, ZTE e
  Nokia).

## 3. Telefonia — essencial para testar a ligação de verdade

- Um **ramal ou rota isolada** no Asterisk do provedor, fora da operação
  em produção, onde configuramos a integração de áudio.
- Caminho mais tranquilo para o primeiro teste: começar num Asterisk de
  homologação (deles ou nosso) e só migrar para produção depois de
  validado.
- Confirmar o codec em uso — G.711a é o padrão no Brasil, e é o que o
  sistema já espera.

## 4. Contas de voz — podemos providenciar

- **Deepgram** (reconhecimento de fala) e **ElevenLabs** (síntese de voz,
  com a voz escolhida junto com o provedor — pode ser a voz de marca
  deles) — podemos criar as contas de teste, ou usar as que o provedor já
  tiver.
- **Claude** (classificação de intenção) — já está coberto do nosso lado.

## 5. Dados para o teste

- 2–3 CPFs de assinantes reais de teste (ou um ambiente de homologação
  com dados fictícios, se o provedor preferir).
- Alguns cenários combinados com antecedência: uma fatura em aberto, um
  relato de "sem internet", uma OS já aberta — para validar os fluxos
  principais logo na primeira rodada.

## 6. Aviso legal e conformidade — antes de qualquer ligação real

- Aviso de gravação e de atendimento automatizado (exigência do Decreto
  11.034/SAC).
- Alinhar com o jurídico do provedor: retenção de dados (LGPD) — do nosso
  lado, CPF já nunca é gravado em claro nos logs.
- Recomendamos parecer jurídico formal antes do go-live com clientes
  reais — não é algo que resolvemos sozinhos, mas apoiamos o que for
  preciso.

---

## O que já está pronto — não espera nada da lista acima

FSM de conversa completa, os 7 principais motivos de ligação (financeiro,
conectividade, operacional) com confirmação verbal antes de qualquer ação
que altere algo na conta do cliente, histórico completo de cada ligação
salvo com segurança, integração real com Hubsoft, GenieACS e Zabbix,
reconhecimento e síntese de voz reais, e a ponte de telefonia já testada
contra um Asterisk de verdade. Mais de 200 testes automatizados
garantindo que tudo isso continua funcionando a cada mudança.

## Como sugerimos rodar o piloto

A própria arquitetura do produto já prevê uma entrada gradual, sem risco
para a operação: começar roteando uma fatia pequena das ligações (ex.:
10%) para o VOX-ISP, comparar contenção/tempo de atendimento/satisfação
contra o fluxo atual, e só depois subir a fatia (30% → 60% → 100%)
conforme os números confirmarem o ganho.

## O que só validamos com o piloto no ar

- Comportamento contra o Hubsoft real (hoje só testado contra a
  documentação pública — a própria Hubsoft não libera ambiente de teste)
- Qualidade de voz e tempo de resposta na ligação real, ponta a ponta
- Volume real de ligações simultâneas
- As 4 partes da conversa por voz funcionando juntas ao vivo (hoje cada
  uma foi validada separadamente)

## Próximo passo concreto para a ISP Direct

O bloco do ERP já está pronto — falta só pedir as credenciais da Hubsoft.
O item que realmente bloqueia é o **OLT Cloud**: como não tem API pública
documentada, o próximo passo é conseguirmos a documentação de integração
direto com a NService antes de construir o adapter. Sem isso, o piloto já
começa útil (financeiro, status de OS, abertura de chamado), só fica sem
diagnóstico óptico/reboot remoto e sem detecção de queda em massa até essa
integração existir.

Detalhes técnicos completos de cada integração ficam em `README.md` e nos
documentos individuais em `docs/` do repositório, para quem quiser entrar
no nível de implementação.
