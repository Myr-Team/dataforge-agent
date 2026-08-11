# DataForge Local Agent FinOps, ROI Regression, and Retrieval Plan

鐘舵€侊細Proposed / local-only
璁″垝鍩虹嚎锛歚5634cf085e96893f4912f8f14033b2c8621321a6`
鍩虹嚎鍒嗘敮锛歚codex/agent-runtime-trace-closure-20260811`
閫傜敤鑼冨洿锛欴ataForge 鏈湴杩愯銆佺绾胯瘎浼板拰鏈湴妫€绱?
涓嶉€傜敤鑼冨洿锛欰PIM銆丄zure 閮ㄧ讲銆乀erraform銆丼QL migration銆佺敓浜ф祦閲忓垏鎹€並iro/Codex 鎺ュ叆

## 1. 鐩殑

鏈鍒掑厛鍦?DataForge 鍐呭缓绔嬩竴濂楀彲楠岃瘉銆佸彲鍥炲綊銆佸彲绉绘鐨勬湰鍦?Agent 杩愯惀鑳藉姏锛岃В鍐充笁涓棶棰橈細

1. 妯″瀷璋冪敤鐨?provider銆乺oute銆乀oken銆乺easoning銆乧ache銆佹垚鏈拰鎴愬姛鐘舵€佽兘鍚﹀舰鎴愬彲淇＄殑鏈湴杩愯浜嬪疄锛?2. 妯″瀷銆丳rompt銆佷笂涓嬫枃銆佽矾鐢辨垨妫€绱㈢瓥鐣ュ彉鏇村悗锛岃兘鍚﹂€氳繃鍥哄畾鏁版嵁闆嗛噺鍖栬川閲忋€佹垚鏈拰鏁堣兘鍙樺寲锛?3. 鐜版湁妫€绱㈣兘鍚﹀湪涓嶇牬鍧?`rag.search()` 鍏煎鎬х殑鍓嶆彁涓嬶紝閫愭鍔犲叆鏈湴 graph-assisted retrieval銆?
鏈樁娈典笉鏄簯閮ㄧ讲宸ヤ綔锛屼篃涓嶅缓绔嬮€氱敤 IDE Agent Gateway銆傝鍒掍骇鍑虹殑鏈湴 observation 涓?evaluation 鍚堝悓搴斾繚鎸佸彲绉绘锛屽悗缁儴缃?Agent 鍙互瀹℃煡鍚庡啀鏄犲皠鍒?APIM銆丼QL 鎴栧叾浠栨簮绔欍€?
## 2. 鏍稿績鍘熷垯

### 2.1 涓夌被浜嬪疄蹇呴』鍒嗙

| 灞傛 | 璐熻矗鍐呭 | 涓嶅厑璁稿仛鐨勪簨 |
| --- | --- | --- |
| `LocalModelObservation` | provider銆乺oute銆乽sage銆乧ache銆乴atency銆乻tatus銆乧ost evidence | 涓嶆帹娴嬩笟鍔℃敹鐩婏紝涓嶆妸鏈煡鍊煎啓鎴愰浂 |
| `EvaluationReport` | 绂荤嚎鍥炲綊銆佹绱㈡帓鍚嶃€乬roundedness銆佸崟浣嶇粡娴庢€ф瘮杈?| 涓嶅啓鍏?verified outcome锛屼笉瀹ｇО鐢熶骇璐ㄩ噺 |
| 鏃㈡湁 Outcome / ROI ledger | observed outcome銆佺嫭绔嬮獙璇併€乿erified ROI | 涓嶆帴鍙楄瘎浼?fixture 鎴栭娴嬬粨鏋滅洿鎺ユ彁鏉?|

### 2.2 APIM 涓嶆槸鏈湴瀹炵幇渚濊禆

鏈湴鑳藉姏浠?DataForge provider response銆乺un event 鍜屾湰鍦?corpus 璇诲彇浜嬪疄銆侫PIM銆丄zure Monitor銆丼QL 鍜?Redis 鍧囦笉寰楁垚涓虹涓€闃舵杩愯鍓嶆彁銆?
### 2.3 鍥炲綊鎹熷け涓嶇瓑浜?ROI

MSE銆丷MSE銆丅CE 鎴?Recall@K 鎻忚堪妯″瀷鎴栨绱㈣川閲忥紝涓嶈嚜鍔ㄧ瓑浜庤储鍔℃敹鐩娿€傚彧鏈夋棦鏈?outcome ledger 涓弧瓒崇嫭绔嬮獙璇併€佹垚鏈畬鏁淬€佸竵绉嶄竴鑷存潯浠剁殑涓氬姟缁撴灉锛屾墠鍙互杩涘叆 verified ROI銆?
### 2.4 榛樿琛屼负蹇呴』鍏煎

- 榛樿妫€绱㈢户缁蛋褰撳墠 `rag.search()` 琛屼负锛?- 榛樿涓嶅惎鍔ㄦ湰鍦拌瘎浼?API锛?- 榛樿涓嶈闂綉缁滐紱
- 榛樿涓嶄骇鐢熺敓浜ц川閲忓０鏄庯紱
- 鏂板瓧娈电己澶辨椂淇濇寔 `null`銆乣unavailable` 鎴?`not_applicable`锛屼笉寰楀洖濉负闆舵垨鎴愬姛銆?
## 3. 褰撳墠鍩虹嚎涓庡凡鐭ョ己鍙?
### 3.1 鍙鐢ㄨ兘鍔?
- `backend/provider_usage.py` 宸插畾涔?DeepSeek reasoning 涓?provider cache token 鐨勬爣鍑嗗寲妯″瀷锛?- `backend/run_store.py` 宸蹭繚鐣?run銆乻tep銆乵odel銆乴atency 鍜?usage锛屽苟鏀寔鏈湴鎸佷箙鍖栵紱
- `RunStoreFinOpsRepository` 鍙湪 SQL 鍏抽棴鏃朵粠鏈湴 run store read-through锛?- `backend/outcome_store.py` 宸插疄鐜?observed outcome 涓庣嫭绔?verification ledger锛?- `backend/roi_service.py` 宸插尯鍒?`estimated`銆乣measured` 鍜?`verified`锛?- `backend/finops/roi_economics.py` 宸叉湁鎯呮櫙娴嬬畻涓庡崟浣嶇粡娴庢ā鍨嬶紱
- `eval/run_maf_runtime_eval.py` 宸叉湁 deterministic CLI銆丣SON report 鍜?groundedness contract锛?- `backend/rag.py` 宸叉湁 local keyword銆丄zure keyword/vector hybrid 鍜岄檷绾ц矾寰勩€?
### 3.2 绗竴闃舵蹇呴』鍏堝叧闂殑鏂偣

1. Foundry usage 鎻愬彇娌℃湁瀹屾暣璇诲彇 reasoning/output detail锛?2. `run_store.record_event()` 娌℃湁鎶婂畨鍏ㄧ殑 `provider_type`銆乣provider_id`銆乣model_id`銆乣provider_cache` 绋冲畾鎶曞奖鍒?`run.models[]`锛?3. FinOps 鍚庣画涓昏璇诲彇 `run.models[]`锛屽洜姝?step/attempt 涓凡鏈夌殑 provider 浜嬪疄浠嶅彲鑳戒涪澶憋紱
4. 浠撳簱娌℃湁閫氱敤鐨?MAE/MSE/RMSE/Huber銆丅CE/Brier銆丷ecall@K/MRR/nDCG 瀹炵幇锛?5. 浠撳簱娌℃湁缁熶竴 retrieval adapter Protocol锛?6. 褰撳墠 Azure Search 璺緞鏄?keyword/vector hybrid锛屼笉鑳芥妸瀹冩弿杩颁负宸插惎鐢?semantic ranker锛?7. 褰撳墠娌℃湁 GraphRAG 鎴?Microsoft Graph Search 鐨勬湰鍦板疄鐜般€?
## 4. 鏈湴鐩爣鏋舵瀯

```text
Provider response
    -> provider usage normalization
    -> model_response event
    -> run_store.models[]
    -> LocalModelObservation extractor
    -> deterministic evaluation runner
    -> EvaluationReport JSON

Workspace corpus
    -> RetrievalRequest
    -> legacy/local/graph-assisted adapter
    -> ranked RetrievalHit[] + trace
    -> retrieval/grounding metrics
    -> EvaluationReport JSON

Outcome store + verification ledger + cost evidence
    -> existing ROI truth gates
    -> optional evaluation comparison
    -> never mutates verified outcome state
```

## 5. 鏈湴鏁版嵁鍚堝悓

### 5.1 `LocalModelObservation`

寤鸿鏂板绾湰鍦板畨鍏ㄦ姇褰憋紝瀛楁濡備笅锛?
```json
{
  "schema_version": "dataforge.local-model-observation.v1",
  "run_ref": "opaque-or-fixture-reference",
  "request_ref": "opaque-or-fixture-reference",
  "workspace_ref": "opaque-or-fixture-reference",
  "agent": "df-feasibility-analyst",
  "capability": "feasibility_analysis",
  "provider_type": "azure_foundry",
  "provider_id": "configured-provider-reference",
  "model_id": "configured-model-reference",
  "route": "primary",
  "deployment": "configured-deployment-reference",
  "route_evidence": "observed",
  "usage": {
    "input_tokens": 100,
    "output_tokens": 20,
    "reasoning_tokens": 7,
    "cached_input_tokens": 40,
    "total_tokens": 120
  },
  "provider_cache": {
    "state": "observed",
    "hit_tokens": 40,
    "miss_tokens": 60
  },
  "latency_ms": 850,
  "status": "completed",
  "cost": {
    "amount": 0.0123,
    "currency": "USD",
    "status": "estimated",
    "price_card_revision": "revision-reference"
  }
}
```

绾︽潫锛?
- 涓嶄繚瀛?prompt銆乺esponse銆乸rovider 鍘熷 body銆佸瘑閽ャ€侀偖绠便€佸師濮?tenant/actor ID锛?- `route_evidence` 鍙兘鏄?`observed`銆乣selected`銆乣inferred` 鎴?`unavailable`锛?- reasoning 鏄?output detail 鏃朵笉寰楀啀娆″姞杩?`total_tokens`锛?- provider cache 涓?DataForge result cache 蹇呴』鍒嗗紑锛?- 瀛楁鏈煡鏃朵繚鎸佺┖鍊煎拰璇佹嵁鐘舵€侊紝涓嶈兘鍥炲～ selected route 骞舵爣涓?observed锛?- 鏈湴 fixture 鍙互浣跨敤鍙 reference锛岀湡瀹?run 蹇呴』浣跨敤鐜版湁瀹夊叏 reference/HMAC 瑙勫垯銆?
### 5.2 `EvaluationCase`

璇勪及鏁版嵁閲囩敤鏄惧紡绫诲瀷锛屼笉浣跨敤涓€涓竾鑳?payload锛?
```json
{
  "schema_version": "dataforge.agent-eval-case.v1",
  "dataset_id": "agent-finops-local",
  "dataset_version": "2026-08-v1",
  "case_id": "continuous-001",
  "task_type": "continuous_regression",
  "capability": "cost_forecast",
  "input_ref": "sanitized-fixture-001",
  "expected": {
    "value": 12.5,
    "unit": "minutes"
  },
  "candidate": {
    "value": 13.1,
    "model_version": "candidate-v1"
  },
  "baseline": {
    "value": 14.0,
    "model_version": "baseline-v1"
  }
}
```

鍏佽鐨?`task_type`锛?
- `continuous_regression`锛歁AE銆丮SE銆丷MSE銆丠uber锛?- `binary_probability`锛欱CE銆丅rier锛屽彲閫?precision/recall/F1锛?- `retrieval_ranking`锛歊ecall@K銆丮RR銆乶DCG@K锛?- `grounded_generation`锛歝laim coverage銆乽nsupported claim rate銆乧itation contract锛?- `unit_economics`锛歝ost per success銆乧ost per verified outcome锛屼粎姣旇緝鍚屽竵绉嶅拰鍚屽彛寰勬暟鎹€?
濡傛灉鈥淏SE鈥濇槸鍏朵粬鍐呴儴鎸囨爣锛屽繀椤诲厛瀹氫箟鍏紡銆佽緭鍏ュ煙銆佽竟鐣屽拰涓氬姟鍚箟锛涘湪姝や箣鍓嶄娇鐢ㄦ爣鍑嗗悕绉?BCE 鎴?Brier锛屼笉鑳藉垱寤哄惈涔変笉娓呯殑 BSE 鎸囨爣銆?
### 5.3 `EvaluationReport`

```json
{
  "schema_version": "dataforge.agent-eval-report.v1",
  "mode": "deterministic_local",
  "measurement_scope": "sanitized_fixture",
  "production_quality_claim": false,
  "dataset": {
    "id": "agent-finops-local",
    "version": "2026-08-v1",
    "digest": "sha256:..."
  },
  "baseline": {
    "version": "baseline-v1",
    "metrics": {}
  },
  "candidate": {
    "version": "candidate-v1",
    "metrics": {}
  },
  "gates": [],
  "sample_count": 0,
  "invalid_count": 0,
  "not_applicable_count": 0,
  "result": "pass"
}
```

鎶ュ憡瑕佹眰锛?
- baseline 鍜?candidate 蹇呴』浣跨敤鐩稿悓 dataset digest锛?- 鎶ュ憡 metric銆乻ample count銆乮nvalid count 鍜?not-applicable count锛?- 绌烘牱鏈笉寰楅€氳繃锛?- 涓嶅悓 unit銆乧urrency銆亀indow 鎴?evidence status 涓嶅緱寮鸿姣旇緝锛?- `generated_at` 鍙互瀛樺湪锛屼絾涓嶅緱鍙備笌缁撴灉 digest锛?- deterministic fixture 鎶ュ憡蹇呴』鍥哄畾 `production_quality_claim=false`銆?
### 5.4 `RetrievalRequest` 涓?`RetrievalHit`

```json
{
  "workspace_id": "workspace-reference",
  "query": "sanitized query",
  "top_k": 5,
  "allowed_corpus_refs": ["corpus-a"],
  "mode": "legacy"
}
```

```json
{
  "id": "chunk-reference",
  "source_file": "safe-source-name",
  "chunk_id": "chunk-12",
  "content": "authorized content",
  "score": 0.84,
  "score_kind": "rrf",
  "rank": 1,
  "adapter": "local_keyword",
  "retrieval_mode": "local_keyword",
  "graph_path_refs": [],
  "retrieval_trace": {
    "candidate_sources": ["local_keyword"],
    "permission_filtered": true
  }
}
```

绾︽潫锛氭潈闄愯繃婊ゅ繀椤诲厛浜庤瀺鍚堝拰 graph expansion锛涗笉寰楀厛鐢熸垚璺ㄦ潈闄愮ぞ鍖烘憳瑕侊紝鍐嶅湪鏈€缁堢粨鏋滃眰杩囨护銆?
## 6. 鎸囨爣涓庤绠楄鍒?
### 6.1 杩炵画鍊煎洖褰?
绗竴鐗堝疄鐜帮細

- `MAE = mean(abs(prediction - target))`锛?- `MSE = mean((prediction - target)^2)`锛?- `RMSE = sqrt(MSE)`锛?- Huber loss 浣跨敤 dataset 鎴?CLI 鏄惧紡鎻愪緵鐨?`delta`銆?
瑙勫垯锛?
- 鎷掔粷 NaN銆両nfinity 鍜岄潪鏁板€艰緭鍏ワ紱
- 鎶ュ憡鏈夋晥銆佺己澶卞拰闈炴硶鏍锋湰鏁帮紱
- 涓嶈兘闈欓粯涓㈠純闈炴硶鏍锋湰鍚庝粛杩斿洖 pass锛?- MAE 浣滀负榛樿鍙В閲婁富鎸囨爣锛孯MSE 鐢ㄤ簬瑙傚療涓ラ噸璇樊锛孒uber 鐢ㄤ簬寮傚父鍊艰緝澶氱殑鏁版嵁銆?
### 6.2 浜屽垎绫绘鐜?
绗竴鐗堝疄鐜帮細

- Binary Cross-Entropy锛?- Brier score锛?- 鍙€?confusion matrix銆乸recision銆乺ecall 鍜?F1銆?
瑙勫垯锛?
- probability 蹇呴』浣嶄簬 `[0, 1]`锛?- BCE 鍙湪璁＄畻 log 鏃朵娇鐢ㄦ樉寮?epsilon clipping锛?- label 鍙兘鏄?0 鎴?1锛?- 涓嶅厑璁告妸 verdict銆乻tatus 鎴栨枃鏈灇涓鹃殣寮忚浆鎹负姒傜巼銆?
### 6.3 妫€绱㈡帓鍚?
绗竴鐗堝疄鐜帮細

- Recall@1/3/5/10锛?- MRR锛?- nDCG@1/3/5/10锛?- 鍙€?evidence/citation precision銆?
瑙勫垯锛?
- qrels 浣跨敤绋冲畾 corpus/chunk reference锛?- relevant set 涓虹┖鏃舵爣璁?`not_applicable`锛屼笉鑳戒吉閫犱负 0 鍒嗭紱
- 閲嶅 hit 鍏堝幓閲嶅啀璁″垎锛?- 姣忎釜 case 淇濈暀 top-k IDs銆乤dapter 鍜?retrieval trace锛?- 鏉冮檺澶?hit 鏄‖澶辫触锛屼笉鍙備笌骞冲潎鍒嗙█閲娿€?
### 6.4 Groundedness

绗竴鐗堝彧鍋氱‘瀹氭€у悎鍚岃瘎浼帮細

- claim 鏄惁甯?evidence refs锛?- ref 鏄惁瀛樺湪浜庤 case 鐨勫厑璁歌瘉鎹紱
- unsupported claim count/rate锛?- reference propagation completeness銆?
璇ユ寚鏍囦笉绛夊悓浜庝汉宸ユ垨 LLM judge 鐨勮涔夋纭€с€傛姤鍛婂繀椤绘槑纭?`measurement_scope=reference_contract`銆?
### 6.5 ROI 涓庡崟浣嶇粡娴?
鍏佽鐨勬湰鍦版瘮杈冿細

```text
Cost per Successful Request = observed comparable cost / successful requests
Cost per Verified Outcome = complete comparable cost / verified outcomes
Net Verified Value = verified monetized benefit - complete cost
Verified ROI = Net Verified Value / complete cost
```

闂ㄧ锛?
- cost 涓嶅畬鏁存椂涓嶈兘璁＄畻 verified ROI锛?- currency 涓嶄竴鑷存椂涓嶈兘鐩稿姞锛?- estimated/scenario outcome 姘歌繙涓嶈兘鍦?eval runner 涓崌绾т负 verified锛?- prediction loss 鐨勬敼鍠勪笉鑳界洿鎺ヨ揣甯佸寲锛?- 鍙湁瀛樺湪鐗堟湰鍖栥€佺粡楠岃瘉鐨勪笟鍔℃槧灏勬椂锛屾ā鍨嬭川閲忓彉鍖栨墠鍙叧鑱?business value銆?
## 7. 瀹炴柦鎵规

### L0锛氭湰鍦拌娴嬩簨瀹為棴鐜?
鐩爣锛氱‘淇濆悗缁洖褰掕緭鍏ュ彲淇°€?
寤鸿鏀瑰姩锛?
1. `backend/foundry_client.py`
   - 鎵╁睍 usage 鎻愬彇锛屾敮鎸?reasoning/output details锛?   - 淇濇寔 provider 鏈繑鍥?usage 鏃朵负 unavailable锛?   - 涓嶄繚瀛?provider 鍘熷鍝嶅簲銆?2. `backend/run_store.py`
   - 灏?allowlisted `provider_type`銆乣provider_id`銆乣model_id`銆乣provider_cache` 鎶曞奖鍒?`run.models[]`锛?   - 淇濈暀 `route_evidence`锛屽尯鍒?observed/selected/inferred锛?   - 淇濇寔鏃?run 鍏煎銆?3. 鏂板 `backend/local_agent_observation.py`
   - 浠?run model record 鏋勯€?`LocalModelObservation`锛?   - 绾嚱鏁颁紭鍏堬紝涓嶈缃戠粶銆佷笉鍐?SQL锛?   - 涓ユ牸娓呮礂瀛楁銆?4. 娴嬭瘯
   - Foundry reasoning fixture锛?   - DeepSeek reasoning/cache fixture锛?   - provider fallback identity锛?   - unknown usage 淇濇寔 null锛?   - provider body銆乸rompt銆乻ecret 涓嶈繘鍏?observation銆?
L0 涓嶄慨鏀?`FinOpsRequestEvent` 鎴?SQL schema銆傞儴缃?Agent 鍚庣画鍐冲畾鍝簺绋冲畾瀛楁杩涘叆鐢熶骇璇锋眰浜嬪疄琛ㄣ€?
### L1锛氭湰鍦板洖褰掍笌 ROI 璇佹嵁姣旇緝

鐩爣锛氭彁渚涙棤缃戠粶銆佸彲閲嶅鐨?baseline/candidate 闂ㄧ銆?
寤鸿鏂板锛?
- `backend/evaluation_metrics.py`锛?- `eval/run_agent_finops_roi_regression.py`锛?- `eval/agent_finops_roi_cases.json`锛?- `tests/test_evaluation_metrics.py`锛?- `tests/test_agent_finops_roi_regression.py`銆?
CLI 鍚堝悓锛?
```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python eval/run_agent_finops_roi_regression.py `
  --mode deterministic `
  --cases eval/agent_finops_roi_cases.json `
  --baseline baseline-v1 `
  --candidate candidate-v1 `
  --output generated-outputs/evaluations/agent-finops-local.json
```

CLI 蹇呴』锛?
- 榛樿鎷掔粷缃戠粶锛?- 鏁版嵁闆?schema 鎴?digest 涓嶅尮閰嶆椂 fail closed锛?- 闈炴硶鏍锋湰瀵艰嚧 gate 澶辫触鎴栨樉寮?incomplete锛?- 閫€鍑虹爜鍖哄垎 pass銆乺egression 鍜?invalid dataset锛?- 杈撳嚭涓嶅寘鍚?prompt銆乺esponse銆佸瘑閽ユ垨鍘熷韬唤锛?- 涓嶈皟鐢?`record_outcome_event()` 鎴?`verify_outcome_event()`銆?
### L2锛氬彲鎻掓嫈鏈湴妫€绱?
鐩爣锛氬湪榛樿琛屼负涓嶅彉鐨勬儏鍐典笅锛屼负 graph-assisted retrieval 寤虹珛鏈湴鎺ュ彛銆?
寤鸿鏂板锛?
- `backend/retrieval_adapters.py`锛?- `tests/test_retrieval_adapters.py`銆?
寤鸿灏忔敼锛?
- `backend/rag.py` 浠呭鍔?adapter factory锛屼繚鎸?`search()` 瀵瑰绛惧悕鍜?legacy fallback锛?- 榛樿 `DF_RETRIEVAL_BACKEND=legacy`锛?- 鏈湴瀹為獙妯″紡鍙娇鐢?`local_keyword` 鎴?`local_hybrid_graph`銆?
Adapter锛?
1. `LegacyRetrievalAdapter`锛氬寘瑁呯幇鏈?`rag.search()` 琛屼负锛?2. `LocalKeywordAdapter`锛氬寘瑁呯幇鏈?`_local_search()`锛?3. `LocalGraphAdapter`锛氳鍙?workspace 鍐呯増鏈寲 JSON/JSONL 鍥撅紱
4. `HybridRetrievalAdapter`锛氬宸叉巿鏉?lexical/graph candidates 鍋?RRF 鍜屽幓閲嶃€?
鏈湴鍥惧缓璁悎鍚岋細

```json
{
  "schema_version": "dataforge.local-corpus-graph.v1",
  "workspace_id": "workspace-reference",
  "corpus_version": "corpus-v1",
  "nodes": [
    {"id": "entity-1", "kind": "entity", "label": "safe label", "evidence_refs": ["chunk-1"]}
  ],
  "edges": [
    {"source": "entity-1", "target": "entity-2", "type": "related_to", "weight": 0.8, "evidence_refs": ["chunk-1"]}
  ]
}
```

L2 鍙仛鏈湴 graph-assisted recall锛屼笉鎺?Microsoft Graph Search銆丟raphRAG 浜戞湇鍔°€丄zure graph database 鎴栨柊 connector secret銆?
### L3锛氶儴缃蹭氦鎺ワ紝闈炴湰鍦板疄鐜拌寖鍥?
L0-L2 楠屾敹閫氳繃鍚庯紝閮ㄧ讲 Agent 鍐嶅喅瀹氾細

- APIM observation 鏄犲皠锛?- SQL schema/migration 涓?retention锛?- Azure Monitor 浣庡熀鏁版寚鏍囷紱
- remote Graph Search/GraphRAG adapter锛?- service identity銆丄CL銆並ey Vault 鍜岀綉缁滆竟鐣岋紱
- canary銆佸洖濉拰鐢熶骇鍥炴粴銆?
## 8. 鍥炲綊闂ㄧ

闂ㄧ鍊肩敱 dataset 鐗堟湰鎺у埗锛屼笉纭紪鐮佷竴濂楅€傜敤浜庢墍鏈変换鍔＄殑鍏ㄥ眬闃堝€笺€?
寤鸿绗竴鐗堜繚瀹堥粯璁ゅ€硷紝浠呯敤浜庢湰鍦?fixture锛?
| 缁村害 | 榛樿闂ㄧ |
| --- | --- |
| Dataset | baseline/candidate digest 蹇呴』涓€鑷?|
| Invalid samples | 蹇呴』涓?0锛屽惁鍒?invalid/incomplete |
| MAE | candidate 涓嶅緱姣?baseline 鎭跺寲瓒呰繃 2% |
| RMSE | candidate 涓嶅緱姣?baseline 鎭跺寲瓒呰繃 5% |
| BCE/Brier | candidate 涓嶅緱鏄捐憲鎭跺寲 |
| Recall@5 | candidate 涓嶅緱涓嬮檷瓒呰繃 0.02 |
| MRR/nDCG@5 | candidate 涓嶅緱涓嬮檷瓒呰繃 dataset tolerance |
| Unsupported claim rate | 涓嶅緱涓婂崌 |
| Permission violations | 蹇呴』涓?0 |
| Cost per success | 璐ㄩ噺鏃犳彁鍗囨椂涓嶅緱涓婂崌瓒呰繃 10% |
| Verified ROI | 鍙兘鍦ㄦ棦鏈夌湡瀹炴€ч棬妲涙弧瓒虫椂灞曠ず |

瀹為檯涓氬姟 dataset 蹇呴』鍦ㄦ枃浠朵腑澹版槑鑷繁鐨?tolerance銆佹牱鏈渶灏忓€煎拰涓绘寚鏍囷紱閮ㄧ讲 Agent 涓嶅簲鐩存帴鎶婁互涓婃湰鍦伴粯璁ゅ€煎綋鐢熶骇 SLO銆?
## 9. Feature flag 涓庡瓨鍌?
### 9.1 绗竴闃舵

- Eval CLI 涓嶉渶瑕佹湇鍔＄ feature flag锛涘彧鏈夋樉寮忔墽琛屾墠杩愯锛?- runtime retrieval adapter 浣跨敤 `DF_RETRIEVAL_BACKEND`锛岄粯璁?`legacy`锛?- 濡傚悗缁鍔犲彧璇?API锛屽繀椤讳娇鐢ㄩ粯璁ゅ叧闂殑 `DF_LOCAL_EVAL_API_ENABLED=0`锛?- graph 鏂囦欢璺緞蹇呴』瑙ｆ瀽鍒?workspace 鏍圭洰褰曞唴锛屾嫆缁濊矾寰勭┛瓒娿€?
### 9.2 鏈湴杈撳嚭

寤鸿杈撳嚭鐩綍锛?
```text
generated-outputs/evaluations/
```

杈撳嚭鏂囦欢涓嶆槸鐢熶骇浜嬪疄婧愶紝涔熶笉鑷姩杩涘叆 Git銆傞渶瑕佽繘鍏ヨ瘎瀹＄殑缁撴灉搴斿鍒朵负缁忚繃鑴辨晱鐨?validation artifact锛屽苟鏄庣‘ dataset銆乧ommit銆佸懡浠ゅ拰 `production_quality_claim=false`銆?
## 10. 瀹夊叏涓庨殣绉佽姹?
- 涓嶈褰?secret銆乧redential銆丄uthorization header锛?- 涓嶈褰?system prompt銆佸畬鏁寸敤鎴?prompt銆佸畬鏁存ā鍨?response锛?- 涓嶈褰曞師濮?tenant銆乤ctor銆乪mail锛?- provider error 鍙娇鐢?allowlisted category锛屼笉澶嶅埗鍘熷閿欒姝ｆ枃锛?- fixture 蹇呴』 synthetic 鎴?sanitized锛?- graph node/edge 蹇呴』鍏宠仈褰撳墠 workspace 鐨?authorized evidence锛?- report 涓彧淇濈暀瀹夊叏 reference 鍜岃仛鍚?metric锛?- 鏈湴 eval 涓嶅緱淇敼 outcome verification ledger锛?- 涓嶅厑璁?fixture銆乨emo seed 鎴?mock 鏁版嵁鍑虹幇鍦?verified ROI 涓€?
## 11. 娴嬭瘯涓庨獙鏀?
### 11.1 L0 楠屾敹

- provider fixture 鈫?usage normalization 鈫?model event 鈫?`run.models[]` 鈫?local observation 涓嶄涪 reasoning/provider/cache锛?- reasoning 涓嶉噸澶嶈鍏?total锛?- provider cache 涓?result cache 鍒嗙锛?- selected/inferred route 涓嶆樉绀轰负 observed锛?- 鏈煡鍊间繚鎸?null/unavailable锛?- 鏁忔劅瀛楁娓呮礂娴嬭瘯閫氳繃锛?- 鏃?run fixture 浠嶅彲璇诲彇銆?
### 11.2 L1 楠屾敹

- 鎵€鏈?metric 浣跨敤宸茬煡灏忔牱鏈獙璇佺簿纭粨鏋滐紱
- NaN/Infinity/绌烘牱鏈?闈炴硶姒傜巼/涓嶅悓鍗曚綅鍜屽竵绉?fail closed锛?- relevant set 涓虹┖鏃朵负 not-applicable锛?- baseline/candidate 鏁版嵁闆嗕笉涓€鑷存椂鎷掔粷姣旇緝锛?- 鐩稿悓杈撳叆閲嶅鎵ц寰楀埌鐩稿悓 metrics銆乬ate 鍜?digest锛?- runner 鍦ㄦ柇缃戠幆澧冧笅瀹屾垚锛?- estimated/scenario 姘镐笉鎴愪负 verified锛?- 鎶ュ憡鏄庣‘ measurement scope 鍜?production claim銆?
### 11.3 L2 楠屾敹

- 榛樿 legacy 琛屼负鍜岃繑鍥炲悎鍚屼笉鍙橈紱
- local adapter 涓嶈闂綉缁滐紱
- graph expansion 鍙兘寮曠敤鎺堟潈 workspace evidence锛?- RRF 鍘婚噸銆佺ǔ瀹氭帓搴忓拰 trace 鍙鐜帮紱
- 鏉冮檺澶?hit 浣?gate 澶辫触锛?- Recall@K/MRR/nDCG 浣跨敤绋冲畾 qrels 璁＄畻锛?- adapter 寮傚父鎸夋槑纭瓥鐣ュ洖閫€锛屼笉鎶婂け璐ユ爣涓烘垚鍔?graph retrieval銆?
### 11.4 寤鸿楠岃瘉鍛戒护

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest -q -p no:cacheprovider `
  tests/test_provider_usage.py `
  tests/test_model_route_telemetry.py `
  tests/test_evaluation_metrics.py `
  tests/test_agent_finops_roi_regression.py `
  tests/test_retrieval_adapters.py

python eval/run_agent_finops_roi_regression.py `
  --mode deterministic `
  --cases eval/agent_finops_roi_cases.json `
  --baseline baseline-v1 `
  --candidate candidate-v1 `
  --output "$env:TEMP\dataforge-agent-finops-eval.json"

git diff --check
```

瀹炵幇瀹屾垚鍚庤繕闇€杩愯鍏ㄩ噺 Python 涓庣幇鏈?Node 娴嬭瘯锛岀‘璁ら粯璁よ涓烘病鏈夊洖褰掋€傜涓€闃舵涓嶉渶瑕?Playwright锛屽洜涓轰笉鏀?UI銆?
## 12. 鏄庣‘绂佹淇敼鐨勮寖鍥?
鏈湴 L0-L2 涓嶅簲淇敼锛?
- `infra/apim/**`锛?- `infra/envs/**`锛?- `infra/modules/**`锛?- `backend/provider_apim.py`锛?- `backend/finops/apim_collector.py`锛?- `backend/finops/apim_backfill.py`锛?- `backend/finops/azure_apim.py`锛?- FinOps SQL DDL/migration锛?- `web/**`锛?- Dockerfile銆乶ginx 鍜岄儴缃茶剼鏈紱
- 鐢熶骇 feature flag銆佹祦閲忔垨浜戣祫婧愩€?
濡傛灉瀹炵幇鍙戠幇蹇呴』淇敼杩欎簺鏂囦欢锛屽簲鍋滄褰撳墠鎵规骞剁敓鎴愭柊鐨勯儴缃茶璁″喅绛栵紝鑰屼笉鏄墿澶ф湰鍦板彉鏇磋寖鍥淬€?
## 13. 寤鸿鎻愪氦鎷嗗垎

鍚庣画瀹炵幇搴斾繚鎸佸彲鐙珛瀹℃煡锛?
1. `local-observation-contract`
   - reasoning/provider/cache 闂幆锛?   - local observation extractor锛?   - 瀹氬悜娴嬭瘯銆?2. `local-evaluation-metrics`
   - pure metrics锛?   - deterministic runner锛?   - fixture dataset 涓庢祴璇曘€?3. `local-retrieval-adapters`
   - adapter Protocol锛?   - legacy/local adapters锛?   - ranking metrics 闆嗘垚銆?4. `local-graph-assisted-retrieval`
   - workspace graph contract锛?   - graph expansion/RRF锛?   - ACL 鍜屽洖褰掓祴璇曘€?
涓嶈灏嗗洓鎵瑰帇鎴愪竴涓悓鏃朵慨鏀?telemetry銆丷OI銆乺etrieval 鍜岄儴缃查厤缃殑澶ф彁浜ゃ€?
## 14. 閮ㄧ讲 Agent 浜ゆ帴娓呭崟

閮ㄧ讲 Agent 鍦ㄦ帴鍏ユ簮绔欏墠搴斿鏌ワ細

### 鏁版嵁鍚堝悓

- `LocalModelObservation` 鍝簺瀛楁鍙互杩涘叆 SQL锛?- provider/model 鏍囪瘑鏄惁闇€瑕?HMAC 鎴栨槧灏勮〃锛?- observed/selected/inferred 鐨勭敓浜у瓨鍌ㄦ柟寮忥紱
- usage/cache/cost 瀛楁涓?APIM 鍝嶅簲鐨勫璐﹁鍒欙紱
- duplicate銆乺etry銆乻tream interruption 鐨勫箓绛夐敭銆?
### 杩愯惀涓庡閲?
- 楂樺熀鏁版槑缁嗚繘鍏ユ棩蹇楄繕鏄姹備簨瀹炶〃锛?- Azure Monitor metric 鐨勪綆鍩烘暟缁村害锛?- retention銆乸artition銆佸洖濉拰鍒犻櫎绛栫暐锛?- evaluation artifacts 鏄惁杩涘叆 Blob锛屼互鍙婅闂寖鍥达紱
- SQL migration 鐨?additive/rollback 璺緞銆?
### 瀹夊叏

- service identity 涓庢渶灏忔潈闄愶紱
- tenant/workspace ACL 鍦ㄨ繙绋嬫绱腑鐨勬墽琛屼綅缃紱
- Graph/Search credential 鐨?Key Vault 绠＄悊锛?- prompt銆乺esponse銆佽韩浠藉拰閿欒姝ｆ枃鐨勭姝㈤噰闆嗛棬绂侊紱
- graph community summary 鏄惁鍙兘璺?ACL 娉勯湶銆?
### 鍙戝竷

- local baseline 鎶ュ憡鍜屽€欓€夋姤鍛婁娇鐢ㄧ浉鍚?dataset digest锛?- canary 鏈熼棿鍚屾椂姣旇緝璐ㄩ噺銆佹垚鏈€丳95 鍜岄敊璇巼锛?- provider usage 缂哄け鏃朵笉浼€犱负闆讹紱
- remote adapter 鍏抽棴鏃跺畬鍏ㄥ洖鍒?legacy锛?- 鏄庣‘鍥炴粴鍛戒护鍜屾祦閲忔仮澶嶆潯浠躲€?
## 15. 寮€鏀惧喅绛?
浠ヤ笅浜嬮」涓嶅湪鏈湴璁″垝涓鍏堝喅瀹氾細

1. provider identity 鏄惁杩涘叆鐜版湁 FinOps SQL 浜嬪疄琛ㄦ垨鐙珛缁磋〃锛?2. APIM token metric 涓庡簲鐢?run event 璋佹槸姣忎釜瀛楁鐨勬潈濞佹潵婧愶紱
3. 杩滅▼ Graph 鑳藉姏鏈€缁堥€夋嫨 Microsoft Graph Search銆丟raphRAG銆丄zure AI Search agentic retrieval 鎴栫粍鍚堟柟妗堬紱
4. semantic ranker 鐨勯厤缃€佹垚鏈拰鍖哄煙鏀寔锛?5. evaluation report 鏄惁闇€瑕?UI锛?6. 閫氱敤鏈湴 Agent Gateway 鐨勫崗璁€佺鍙ｃ€侀壌鏉冨拰 Kiro/Codex adapter銆?
杩欎簺鍐崇瓥搴旂敱鏈湴 L0-L2 鐨勭湡瀹炴姤鍛婂拰閮ㄧ讲鐜绾︽潫椹卞姩锛岃€屼笉鏄幇鍦ㄦ彁鍓嶈€﹀悎銆?
## 16. 瀹屾垚瀹氫箟

鏈湴鑳藉姏杈惧埌鍙氦浠樼姸鎬侊紝蹇呴』鍚屾椂婊¤冻锛?
- L0 observation 閾捐矾鍦?fixture 涓笉涓?provider銆乺easoning 鍜?cache锛?- L1 runner 鍙湪鏃犵綉缁滅幆澧冮噸澶嶈繍琛屽苟姝ｇ‘闃绘柇鍥炲綊锛?- ROI 鐪熷€艰竟鐣屾湭琚瘎浼版垨棰勬祴缁曡繃锛?- L2 榛樿鍏抽棴鏃剁幇鏈夋绱㈣涓轰笉鍙橈紱
- 鎵€鏈夋柊澧炲悎鍚屽拰鎶ュ憡涓嶅惈鏁忔劅鍐呭锛?- 瀹氬悜娴嬭瘯銆佸叏閲忔祴璇曞拰 `git diff --check` 閫氳繃锛?- 娌℃湁淇敼 APIM銆乀erraform銆丼QL銆乁I 鎴栭儴缃叉枃浠讹紱
- 閮ㄧ讲 Agent 鍙互浠呬緷鎹湰鏂囦欢銆佷唬鐮?diff 鍜?validation report 瀹屾垚鐙珛瀹℃煡銆?