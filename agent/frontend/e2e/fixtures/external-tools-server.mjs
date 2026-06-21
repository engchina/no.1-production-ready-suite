import http from "node:http";

const portArgIndex = process.argv.indexOf("--port");
const port = portArgIndex >= 0 ? Number(process.argv[portArgIndex + 1]) : 8052;

function readBody(request) {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
    });
    request.on("end", () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (error) {
        reject(error);
      }
    });
    request.on("error", reject);
  });
}

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
  });
  response.end(JSON.stringify(payload));
}

const server = http.createServer(async (request, response) => {
  if (request.method === "GET" && request.url === "/health") {
    sendJson(response, 200, { status: "ok" });
    return;
  }
  if (request.method === "POST" && request.url === "/query") {
    const payload = await readBody(request);
    sendJson(response, 200, {
      sql: "select department, revenue from sales_summary order by revenue desc",
      columns: [
        { name: "department", type: "string", label: "部門" },
        { name: "revenue", type: "number", label: "売上", unit: "JPY" },
      ],
      rows: [
        { department: "法人営業", revenue: 1250000 },
        { department: "カスタマーサクセス", revenue: 780000 },
      ],
      row_count: 2,
      truncated: false,
      execution_time_ms: 18,
      lineage: { service: "playwright-fixture", trace_id: payload.trace_id ?? null },
      warnings: [],
      metadata: { mode: payload.mode ?? "execute", limit: payload.limit ?? null },
    });
    return;
  }
  if (request.method === "POST" && request.url === "/search") {
    const payload = await readBody(request);
    sendJson(response, 200, {
      answer: `${payload.query ?? "質問"} の検証用回答です。`,
      contexts: [
        {
          id: "ctx-1",
          title: "検証用文書",
          content: "外部 RAG の文脈を標準形式で返します。",
          score: 0.92,
        },
      ],
      citations: [
        {
          source_id: "doc-1",
          title: "検証用文書",
          url: "https://example.test/doc-1",
          page: 1,
        },
      ],
      metadata: { service: "playwright-fixture" },
    });
    return;
  }
  if (request.method === "POST" && request.url === "/jsonrpc") {
    const payload = await readBody(request);
    if (payload.method === "tools/list") {
      sendJson(response, 200, {
        jsonrpc: "2.0",
        id: payload.id ?? "mcp-list",
        result: {
          tools: [
            {
              name: "lookup_customer",
              description: "顧客情報を検索する",
              inputSchema: {
                type: "object",
                properties: {
                  customer_id: { type: "string" },
                },
              },
              outputSchema: { type: "object" },
              serverId: payload.params?.server_id ?? "crm",
              metadata: { fixture: true },
            },
            {
              name: "search_orders",
              description: "受注を検索する",
              inputSchema: {
                type: "object",
                properties: {
                  account_id: { type: "string" },
                  limit: { type: "number" },
                },
              },
              outputSchema: { type: "object" },
              serverId: payload.params?.server_id ?? "erp",
              metadata: { fixture: true },
            },
          ],
        },
      });
      return;
    }
    if (payload.method === "tools/call") {
      sendJson(response, 200, {
        jsonrpc: "2.0",
        id: payload.id ?? "mcp-call",
        result: {
          content: [{ type: "text", text: "fixture MCP tool result" }],
          structuredContent: { ok: true },
        },
      });
      return;
    }
    sendJson(response, 200, {
      jsonrpc: "2.0",
      id: payload.id ?? null,
      error: { code: -32601, message: "method not found" },
    });
    return;
  }
  sendJson(response, 404, { error: "not_found" });
});

server.listen(port, "127.0.0.1");

function shutdown() {
  server.close(() => process.exit(0));
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
