# Rancher MCP Server

Servidor [MCP (Model Context Protocol)](https://modelcontextprotocol.io) que expõe clusters gerenciados pelo [Rancher](https://www.rancher.com/) como ferramentas para assistentes de IA. Na prática: um copiloto de SRE. Você pergunta "por que o serviço de inferência está reiniciando?" e o assistente inspeciona clusters, workloads, pods, logs e eventos para responder.

> MCP server that exposes Rancher-managed clusters as tools for AI assistants, turning Claude into an SRE copilot.

## Ferramentas

| Tool | Descrição | Modo |
|---|---|---|
| `list_clusters` | Clusters com estado, versão e capacidade | leitura |
| `list_nodes` | Nós com papéis, estado e recursos | leitura |
| `list_projects` | Projetos Rancher de um cluster | leitura |
| `list_workloads` | Workloads de um projeto | leitura |
| `list_pods` | Pods, com filtro `only_unhealthy` (CrashLoop, Pending, +3 restarts) | leitura |
| `get_pod_logs` | Últimas N linhas de log (inclui container anterior com `previous=True`) | leitura |
| `get_events` | Eventos recentes do cluster (Warnings por padrão) | leitura |
| `diagnose_workload` | Diagnóstico em uma chamada: estado + pods doentes + eventos | leitura |
| `scale_workload` | Escala um workload | escrita (gated) |
| `redeploy_workload` | Redeploy rolling | escrita (gated) |

## Segurança

- **Somente leitura por padrão.** `scale_workload` e `redeploy_workload` só funcionam com `RANCHER_MCP_ALLOW_WRITE=true`.
- Escala limitada por `RANCHER_MCP_MAX_REPLICAS` (padrão 20), evitando um "scale to 5000" acidental.
- Credenciais apenas via variáveis de ambiente. Nada de token em arquivo de configuração ou código.
- Respostas resumidas: as tools devolvem os campos que um SRE precisa, não o payload bruto da API.

## Instalação

```bash
git clone https://github.com/flaviagaia/rancher-mcp-server.git
cd rancher-mcp-server
pip install -e .
```

Crie um token de API no Rancher (avatar > Account & API Keys > Create API Key; prefira escopo por cluster) e exporte:

```bash
export RANCHER_URL="https://rancher.example.com"
export RANCHER_TOKEN="token-xxxxx:yyyyyyyyyyyy"
# opcional:
# export RANCHER_MCP_ALLOW_WRITE=true
# export RANCHER_MCP_MAX_REPLICAS=10
# export RANCHER_VERIFY_TLS=false   # apenas para labs com certificado self-signed
```

## Uso com Claude

**Claude Code / Cowork:**

```bash
claude mcp add rancher --env RANCHER_URL=$RANCHER_URL --env RANCHER_TOKEN=$RANCHER_TOKEN -- rancher-mcp-server
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "rancher": {
      "command": "rancher-mcp-server",
      "env": {
        "RANCHER_URL": "https://rancher.example.com",
        "RANCHER_TOKEN": "token-xxxxx:yyyyyyyyyyyy"
      }
    }
  }
}
```

Exemplos de conversa:

- "Liste meus clusters e me diga se algum está degradado."
- "Quais pods estão em crash loop no projeto Default?"
- "Diagnostique o workload deployment:ml-serving:inference-api e me mostre os logs do container que caiu."

## Sem um Rancher à mão?

Suba um em um container para testar:

```bash
docker run -d --name rancher --privileged -p 8443:443 rancher/rancher:latest
# depois: export RANCHER_URL=https://localhost:8443 e RANCHER_VERIFY_TLS=false
```

## Testes

```bash
pip install -e ".[dev]"
pytest
```

Os testes usam um cliente mockado; não precisam de um Rancher real.

## Roadmap

- Métricas de uso (CPU/memória por workload) para sugestões de rightsizing
- Suporte a Fleet (GitOps) e Rancher apps
- Tool de comparação de estado entre clusters (staging vs prod)

## Licença

MIT. Feita com carinho por [Flávia Gaia](https://github.com/flaviagaia).
