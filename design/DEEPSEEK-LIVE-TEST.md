# DeepSeek and Feishu Live Test

Date: 2026-09-07
Local application: http://127.0.0.1:8000

## Configuration

- Provider: OpenAI-compatible DeepSeek API, https://api.deepseek.com
- Model: deepseek-v4-flash
- The user's key was submitted through hidden terminal input and the existing local model configuration API. It is stored in the git-ignored `.env`, with file permissions restricted to 0600. No key is included in this report.
- Dedicated Feishu application: Feishu CLI Web 测试 20260907, app ID `cli_aa140c4ff8b89cbd`.
- Web account: admin; Feishu identity observed in Chrome: dear.
- The broad recommended authorization flow was not completed. A separate authorization for `docx:document:create`, `docx:document:write_only`, and `docx:document:readonly` completed successfully. Optional bundled permissions were unchecked.

## Verified Results

1. DeepSeek `/models` returned HTTP 200 and included the exact requested model.
2. A direct completion returned HTTP 200, model `deepseek-v4-flash`, and `DEEPSEEK_TEST_OK`. Usage for this probe: 92 prompt tokens, 26 completion tokens, 118 total. Other test calls also consumed tokens; no full-session cost was calculated.
3. The application's template generation endpoint returned HTTP 200 and generated a structured private-document workflow with four fields: test title and three test results. The output differed from the application's local fallback template.
4. The application's Feishu setup status reported `ready: true` after browser authorization.
5. The chat plan required confirmation before creating a document. The authorized chat execution returned success and an actual Feishu document ID.
6. The document was opened in the user's connected Chrome and its title and three body paragraphs were verified.
7. A read-only `docs +fetch` call through the application's chat endpoint returned the same title and paragraphs, revision 3.
8. Chrome sharing controls initially showed organization link access. Link sharing was turned off; the UI confirmed that only collaborators can access it. No collaborators were invited and no messages were sent.

## Retained Test Resource

- Title: Feishu CLI Web 测试 20260907
- Document ID: `Kdqvd2pNboqlmRxdeXrcD4N4nJb`
- URL: https://ucnn92h1qs1c.feishu.cn/docx/Kdqvd2pNboqlmRxdeXrcD4N4nJb
- Screenshot: `/tmp/feishu-deepseek-live-document.png`
- Body: DeepSeek 模型连接成功; 模板生成成功; 飞书文档创建测试.

## Fixes Found During Testing

- Explicit Chinese titles using `标题为` were missed by the deterministic document planner. The planner now recognizes them and removes a leading body delimiter.
- The planner emitted legacy `--markdown` arguments and the repair pass changed valid `--content` into that legacy flag. New plans use `--doc-format markdown --content`, explicitly select user identity, and the repair pass converts legacy flags while preserving valid XML commands.
- During live execution, the agent produced a successful v2 DocxXML creation command. The retained resource and fetched content verify the final result; this test does not establish that every planned command is executed unchanged.
- Two regression tests cover title/content extraction and command repair. Full backend suite: 5 passed, with one upstream AnyIO deprecation warning. Targeted lint and whitespace checks passed.

## Scope

Verified model access, structured generation, application authorization, document creation, and document readback. Calendars, messages, task assignment, scheduled execution, and presentation generation were not exercised. The test application and document are retained for inspection.
