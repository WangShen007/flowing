# Execution-chain figure brief
Goal: replace the README Mermaid diagram with an elegant Chinese research-style figure.
Audience: repository readers. Source of content and graph: README section 一条可追踪的执行链 and its adjacent executor paragraph.
Must preserve: natural-language input, capability/parameter discovery, live Feishu reads, write decision, explicit confirmation/authorization, execution receipt, refusal stop, returned result, optional workflow memory.
Excluded: invented metrics, model training modules, automatic retries, new product behavior.
Traceability: A→B→C→D; D(no)→E; D(yes)→F; F(confirm/authorize)→G→E; F(refuse)→H; E→I(optional).
All arrows are directed control/data progression, one-to-one per edge. Only E→I is optional and dashed. No connector may cross an unrelated node or text.
Assumption: research-figure style means restrained vector composition, not a claim of scientific novelty.
