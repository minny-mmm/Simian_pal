# คู่มือใช้เดโมและเชื่อม frontend

สำหรับทีมและผู้สาธิต อ้างอิงโค้ด/config ของ `reference-demo-v1` และผลตรวจที่บันทึกวันที่ 24 ต.ค. 2026 คู่มือนี้ไม่ได้ตรวจสถานะ AWS ใหม่หรือ deploy ซ้ำ

เดโม: **https://lvd7t7j977.execute-api.ap-southeast-1.amazonaws.com**

URL นี้ออกหลัง resume วันที่ 24 ต.ค. 2026; URL `a7cklo11v6` ใน handoff เดิมถูกลบโดย stop แล้ว ดู URL ล่าสุดจาก `deployment.json` เสมอ

ระบบบน AWS ใช้ baseline ที่ **fit ในเครื่องจากข้อมูลจริง** ของรอบ `aws-real-pilot-20261024-v1` การ deploy นี้ไม่ได้รัน training บน SageMaker ดูรายละเอียดเชิงระบบใน [README_reference_deployment.md](README_reference_deployment.md)

## 1. เปิดและใช้งานเดโม

1. เปิด URL ด้านบน คอมพิวเตอร์เจ้าของโปรเจกต์ไม่ต้องเปิดค้าง
2. ขอรหัสเข้าถึงจากผู้ดูแลทีมผ่านช่องทางส่วนตัว ผู้มีสิทธิ์ AWS ดึงรหัสลง clipboard ได้ด้วยคำสั่งด้านล่าง
3. วางรหัสในช่อง **รหัสเข้าถึงเดโม** แล้วกด **เข้าสู่ระบบ** ครั้งเดียว รอโหลดครั้งแรกประมาณ 15 วินาทีก่อนลองอีกครั้ง ตัวเลขนี้เป็นแนวทางรอ ไม่ใช่ SLA
4. เลือก `VALID-A` หรือ `VALID-B` เพื่อดูกราฟและตารางรายวัน ปุ่ม **โหลดผล** จะเรียก API ใหม่
5. เลือกวัน แล้วกด **เรียกคำอธิบาย** ตรวจป้ายว่ามาจาก LLM จริงหรือ template fallback
6. เปิด **Analysis JSON** เพื่อดูค่าต้นทางของตาราง กด **ออกจากระบบ** เมื่อใช้งานเสร็จ

```powershell
# สำหรับสมาชิกที่มีสิทธิ์อ่าน secret เท่านั้น ไม่ต้องทำทุกครั้งหากมีรหัสแล้ว
aws sso login --profile minny --region ap-southeast-1
aws secretsmanager get-secret-value --secret-id pd-reference-demo-v1-login --query SecretString --output text --profile minny --region ap-southeast-1 | Set-Clipboard
```

อย่านำรหัสไปใส่ในแชต, Git, URL หรือภาพหน้าจอ Browser เก็บรหัสในหน่วยความจำของหน้าเท่านั้น เมื่อ reload/ปิดหน้าอาจต้องเข้าสู่ระบบใหม่ ไม่ต้องใช้ AWS SSO ใน browser ของผู้ชม และไม่ต้องมี Bedrock API key

**API ข้อมูลหมดอายุ 27 ต.ค. 2026 เวลา 08:52 น. ไทย** ตาม release ปัจจุบัน หลังจากนั้นหน้า login อาจยังเปิดได้แต่ API จะคืน `410 demo_expired` การต่ออายุต้องให้ผู้ดูแลออก release อย่างชัดเจน

## 2. อ่านผลอย่างไร

เดโมโหลด validation ที่อนุญาตไว้สองคน รวม 11 subject-days / 33 แถวตัวชี้วัด มี `ok` 26 แถว และ `insufficient_data` 7 แถว นามแฝงไม่ได้ทำให้ข้อมูลเป็นข้อมูลสาธารณะ ห้ามแชร์ผลหรือรหัสออกนอกทีมโดยไม่มีอนุญาต

| ชื่อฟิลด์ `metric` | ความหมาย | หน่วย API |
| --- | --- | --- |
| `enmo_hourly_median_mean_g` | ENMO รายวันจากค่า median รายชั่วโมง ถ่วงน้ำหนักด้วยจำนวนหน้าต่าง | `g` |
| `walking_cadence_hourly_median_mean_hz` | Cadence ขณะเดิน จาก median รายชั่วโมงถ่วงน้ำหนักด้วยจำนวนหน้าต่างเดิน | `steps/s` |
| `rest_tremor_window_fraction` | สัดส่วนหน้าต่างช่วงพักที่เข้าเกณฑ์ tremor | `fraction` ช่วง 0–1 |

- กราฟสีน้ำเงินคือค่ารายวัน สีส้มคือ median อ้างอิง COPS สีเขียวคือ median อ้างอิง NHANES
- `signed_distance = value - reference_median`: บวกคือสูงกว่ากลุ่มอ้างอิง ลบคือต่ำกว่า หน่วยเดียวกับตัวชี้วัด
- `standardized_deviation` คือระยะห่างหารด้วย reference scale ตาม implementation ไม่ใช่ความน่าจะเป็นของโรค หาก scale ใช้ไม่ได้ให้คง `undefined_scale` และ `null`
- `previous_day.delta` คือวันนี้ลบวันก่อนหน้าตามดัชนีวันจริง ไม่กระโดดไปเทียบวันล่าสุดที่มีข้อมูล
- `null` หมายถึงคำนวณไม่ได้ อ่าน `status`, `qc.reasons` และ reason ของ comparison/previous day ประกอบ ห้ามแทนด้วย 0 หรือเชื่อมเส้นกราฟข้ามช่องว่าง
- API ใช้ `day` เริ่มจาก 0 หน้าเดโมแสดง `day + 1` ไม่ใช่วันที่ปฏิทิน และไม่ควรสร้างวันที่ขึ้นเอง
- `is_demo=false`, `data_origin=real` หมายถึงข้อมูลวัดจริง แม้หน้าเว็บนี้ใช้เพื่อสาธิต

ไม่รวมตัวชี้วัดเป็นคะแนนเดียว และไม่แปลระยะห่างเป็นระดับความรุนแรงหรือโอกาสเป็น PD สอง dataset ใช้อุปกรณ์ต่างกัน ความต่างอาจสะท้อนอุปกรณ์/ประชากรด้วย

คำอธิบายมีสองแบบ: `generation_kind=llm` เป็นข้อความจาก Nova Lite ที่ผ่าน guard ของ backend ส่วน `expected_template` เป็นคำอธิบายตาม template ที่ระบุ fallback ชัดเจน ทั้งสองแบบต้องให้คนตรวจความหมาย ตัวเลขที่ Dashboard แสดงต้องมาจาก Analysis JSON ไม่ดึงจากข้อความ LLM

## 3. ข้อจำกัดทรัพยากรและค่าใช้จ่าย

| รายการ | ค่าของรุ่นนี้ / ผลที่ผู้ใช้ควรรู้ |
| --- | --- |
| Backend | Lambda Python 3.12, RAM 512 MB, temporary disk 512 MB, timeout 30 วินาที |
| ผลวัด AWS ที่บันทึกไว้ | RAM สูงสุด 189 MB; warm status 71.17 ms; HTTP โหลด status ครั้งหนึ่ง 13.93 วินาที รวม network/cold loading ไม่ใช่ benchmark รับประกัน |
| API Gateway | 1 request/วินาที, burst 5 แชร์ระหว่างผู้ใช้ |
| Authenticated API | รวมทุกคน 100 requests/วัน ไม่ใช่ต่อผู้ใช้; status/analysis/explanation นับด้วย รีเซ็ตตามวัน UTC (07:00 น. ไทย) |
| LLM | รวม 20 attempts/วัน; สูงสุด 3 metric rows, prompt 8,000 UTF-8 bytes, output 1,000 tokens/ครั้ง; synthetic probe ใช้โควตาเดียวกัน |
| Cache | คำอธิบายเดิมอาจอ่านจาก cache จึงไม่เรียก LLM ใหม่ แต่ยังนับ API request; attempt ที่ล้มเหลวอาจใช้โควตา/มีค่าใช้จ่าย |
| Concurrency | บัญชีมี Lambda concurrency 10 แชร์กับงานอื่น ไม่มี reserved concurrency สำหรับฟังก์ชันนี้ |
| ขอบเขตข้อมูล | อ่านเฉพาะ valid bundle ที่ตรึงไว้ ไม่มี upload ผู้ป่วยใหม่, streaming หรือ endpoint วิเคราะห์ payload ใหม่ |
| Bedrock | `apac.amazon.nova-lite-v1:0` ผ่าน runtime IAM; APAC cross-region inference อาจประมวลผลนอก Singapore |

หน้าเว็บสาธารณะและ traffic ที่ไม่ผ่าน login ยังอาจมีค่าใช้จ่าย เพดานคำขอในแอปไม่ใช่ hard billing cap เดโมนี้ไม่เหมาะกับโหลดทดสอบหรือการเปิดใช้พร้อมกันจำนวนมาก หลีกเลี่ยง auto-refresh, polling, prefetch และการกดคำอธิบายทุกวันอัตโนมัติ

ประมาณการที่บันทึกในการส่งมอบ: **USD 0.08–0.23 / 72 ชั่วโมง** สำหรับเดโมเบา ๆ รวมสมมติฐาน LLM 20 ครั้ง/วัน ครั้งละ 2,000 input + 1,000 output tokens ไม่รวมภาษี traffic เกินสมมติฐานหรือทรัพยากรเดิม ไม่ใช่ยอดเรียกเก็บจริง และยังไม่ยืนยันว่าเครดิตครอบคลุม รายละเอียดราคา/หลักฐานอยู่ใน README deployment กับรายงานเดิม

## 4. ให้ frontend ของเพื่อนเชื่อมอย่างไร

**ปัจจุบันรองรับ same-origin เท่านั้น ยังไม่มี CORS สำหรับเว็บเพื่อน** การใส่ URL API นี้ใน React/Vite ที่รัน `localhost` แล้ว `fetch` ตรง ๆ จะติด cross-origin โดยเฉพาะ preflight จาก Authorization header อย่าแก้ด้วย `mode: 'no-cors'` เพราะจะอ่าน JSON ไม่ได้

แนวทางแนะนำสำหรับ Next.js หรือ frontend ที่มี backend/BFF ของตัวเอง:

```text
Browser ของทีม
  -> /api/reference/* บนโดเมน frontend ของเพื่อน (ตรวจ session ผู้ใช้)
  -> server proxy แนบ Authorization: Bearer <รหัสเดโม>
  -> API Gateway ของเดโม -> Lambda -> private baseline/valid bundle + Bedrock
```

**Server proxy นี้ยังไม่ได้สร้างใน repository นี้** ให้เพื่อนเพิ่มใน frontend/backend ของเขาโดยใช้สัญญาด้านล่าง ไม่ต้องย้ายหรือ fit baseline ใหม่

1. ตั้ง server-only configuration เช่น `REFERENCE_API_BASE_URL=https://lvd7t7j977.execute-api.ap-southeast-1.amazonaws.com` และ `REFERENCE_DEMO_TOKEN` ผ่าน secret/runtime environment ของ server ไม่ใส่ค่ารหัสในไฟล์ที่ commit
2. ตรวจ session/สิทธิ์ผู้ใช้ก่อน proxy ทุกครั้ง มิฉะนั้นจะกลายเป็นทางเข้าข้อมูลโดยข้าม login ห้ามเปิด proxy สาธารณะโดยถือว่ามี server token แล้วปลอดภัย
3. อนุญาตเฉพาะ GET ไป `status`, `analysis`, `explanation` และ query ที่กำหนด ไม่รับ upstream URL จาก browser ไม่เปิด `/llm-probe` ให้ผู้ชม
4. Server ใส่ Authorization เอง ไม่ส่ง token กลับ browser ไม่ log header และไม่ใช้ชื่อตัวแปร `NEXT_PUBLIC_*` หรือ `VITE_*` สำหรับ secret
5. ส่ง JSON/status กลับด้วย `Cache-Control: no-store` ไม่ cache ข้อมูลบน CDN ตั้ง upstream timeout ราว 35 วินาทีเพื่อรองรับ cold start โดยตรวจ hosting timeout ของ server เพื่อนด้วย
6. ไม่ retry คำขออัตโนมัติไม่จำกัด เนื่องจาก request อาจถูกประมวลผลไปแล้วและกินโควตา ให้แสดง error และปุ่มลองใหม่แก่ผู้ใช้

ถ้าเพื่อนมีเฉพาะ static React/Vite ให้เพิ่ม server proxy ที่มี authentication ก่อน หรือรวม frontend เข้ากับ backend โดเมนเดียวกันผ่าน release ที่ตรวจแล้ว Lambda รุ่นนี้เสิร์ฟเฉพาะ `/` และ `/app.js` จึงยังไม่ได้รองรับการอัปโหลด build assets ของ frontend ใด ๆ โดยตรง

การเปิด CORS แบบ allowlist เป็นอีกทางเลือก แต่ต้องออกแบบ authentication/preflight และแก้ deployment ก่อน ปัจจุบันยังไม่ได้เปิด อย่าใช้ wildcard หรือเผยรหัสร่วมไว้ใน frontend bundle

### สัญญา API ที่ใช้ได้แล้ว

ทุก endpoint ในตารางต้องมี `Authorization: Bearer <รหัสเดโม>` ระหว่าง server กับ API

| GET endpoint | ผลลัพธ์ |
| --- | --- |
| `/api/status` | `schema_version`, `baseline_version`, `baseline_sha256`, `baseline_fit_location`, `llm_enabled`, `subject_aliases` และสถานะ training ของ deployment |
| `/api/analysis?subject=VALID-A` | Analysis document มี `schema_version` และ array `analysis`; เลือก `VALID-B` ได้; ถ้าไม่ส่ง subject จะได้ทั้งสองคน |
| `/api/explanation?subject=VALID-A&day=6` | Envelope `{analysis, explanation, validation}`; day เป็นค่า API ไม่ใช่ค่าที่แสดง +1; ถ้าไม่ส่ง day ใช้วันล่าสุดของคนนั้น |

เลือก day จากข้อมูลที่ API ส่งกลับ อย่า hardcode ว่าทุกคนมีวันเหมือนกัน Public Analysis/Explanation ใช้ schema version **`1.0.0`** ส่วน status/error/envelope เป็น adapter contract ที่อธิบายในตาราง ไม่ควรเหมาว่าเป็น Analysis schema เดียวกัน

ตัวอย่าง client หลังเพื่อนติดตั้ง authenticated proxy ตามข้างต้นแล้ว (path `/api/reference/*` เป็น path ที่เสนอ ยังไม่ใช่ route ของเดโมปัจจุบัน):

```javascript
async function referenceGet(resource, params = {}) {
  const query = new URLSearchParams(params);
  const response = await fetch(`/api/reference/${resource}?${query}`, {
    credentials: 'same-origin', cache: 'no-store'
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

const status = await referenceGet('status');
if (status.schema_version !== '1.0.0') throw new Error('Unsupported schema');
const analysis = await referenceGet('analysis', {subject: 'VALID-A'});
// ส่ง analysis.analysis ไปวาดกราฟ เก็บ null เป็นช่องว่าง
// เรียก explanation เมื่อผู้ใช้กดปุ่ม ไม่เรียกล่วงหน้าทุกวัน
const row = analysis.analysis.at(-1);
if (row) {
  const result = await referenceGet('explanation', {
    subject: row.subject_id, day: String(row.day)
  });
  const isModel = result.explanation.generation_kind === 'llm';
  // แสดงป้าย isModel ? 'LLM' : 'Template fallback' ควบคู่ข้อความ
}
```

ฟิลด์ที่ frontend ต้องรักษา: `value`, `unit`, `status`, `coverage`, `qc.reasons`, `reference_comparisons.cops/nhanes`, `previous_day` และ `baseline_version` ใช้ `signed_distance`/`standardized_deviation` ที่ backend คำนวณแล้ว ไม่คำนวณสูตรใหม่ใน browser ถ้าแสดง fraction เป็น % ให้คูณ 100 เฉพาะการแสดงผลและติดหน่วยให้ชัด โดยไม่แก้ค่า JSON ต้นทาง

ตัวอย่าง JSON จริงที่บันทึกไว้ในเครื่องผู้ deploy (เป็นข้อมูลควบคุมการเข้าถึง ไม่ควรเอาไปใส่ public frontend repository):

- `reports/deployment/reference-demo-v1/analysis_DEPLOYED_REAL.json`
- `reports/deployment/reference-demo-v1/explanation_DEPLOYED_REAL.json`
- `reports/deployment/reference-demo-v1/explanation_MODEL.json`

### ตรวจรับ integration ของเพื่อน

- ผู้ไม่ login อ่านผลไม่ได้ และ browser Network/source bundle ไม่มี server token หรือ AWS credentials
- เลือกคนแล้วข้อมูล/วันเปลี่ยนตาม API; day บนจอ +1 แต่ query ใช้ดัชนีเดิม
- ค่า null แสดงเหตุผลและเว้นกราฟ; เครื่องหมาย delta/หน่วยตรง Analysis
- แสดง baseline fit location `local` และแยก `llm` จาก `expected_template`
- รองรับ 401/410/429/503 ไม่ polling หรือเรียก LLM วน; ปุ่มมี loading/disable ขณะรอ
- การอ่านผลไม่เปิด test และไม่ส่ง validation เข้า fit; ไม่มีรหัสหรือข้อมูลจริงถูก commit

## 5. แก้ปัญหาและปิดเดโม

| อาการ / HTTP | วิธีตรวจ |
| --- | --- |
| หน้าเปิดได้แต่ login ไม่ผ่าน / 401 | ตรวจรหัสจาก secret ชื่อถูกต้อง ไม่มีช่องว่าง/บรรทัดใหม่ และไม่ใช่รหัส AWS SSO |
| รอนานตอนเข้าใช้ครั้งแรก | รอ cold start อย่ากดซ้ำหลายครั้ง; timeout/503 ให้ผู้ดูแลดู CloudWatch |
| 429 | อาจชน Gateway throttle หรือเพดาน API รายวัน อ่าน body ประกอบ; daily limit ให้รอวัน UTC ใหม่ ไม่ยิง retry วน |
| คำอธิบายขึ้น template | อ่าน `generation_kind` และ `validation.reason` อาจเป็นข้อจำกัด LLM/สิทธิ์/guard/โควตา ไม่ได้แปลว่าตัวเลข Analysis เป็นข้อมูลจำลอง |
| 410 `demo_expired` | หมดอายุ release ให้ผู้ดูแลพิจารณาต่ออายุ ไม่ใช่แก้ที่ frontend |
| 400 / 404 | ตรวจ alias และ day จริง; `invalid_or_incompatible_request` อาจต้องให้ผู้ดูแลตรวจ config/artifact ด้วย |
| เว็บเพื่อน fetch ตรงไม่ได้ แต่เดโมเปิดได้ | ตรวจ CORS และใช้ authenticated server proxy ตามหัวข้อ 4 |
| 503 `service_unavailable` | ผู้ดูแลตรวจ CloudWatch `/aws/lambda/pd-reference-demo-v1` และสิทธิ์อ่าน artifact/config; ไม่ปิด provenance/hash validation เพื่อแก้ปัญหา |

คำสั่งตรวจ end-to-end สำหรับผู้ดูแล (ใช้ API หลาย requests และ synthetic Bedrock probe หนึ่งครั้งซึ่งใช้โควตา/อาจมีค่าใช้จ่าย ไม่ควรรันวน):

```powershell
.venv/Scripts/python.exe tools/check_reference_demo.py
```

เมื่อเลิกใช้งาน ให้ผู้ดูแลรันจาก repository:

```powershell
.venv/Scripts/python.exe tools/deploy_reference_demo.py --stage stop
```

คำสั่งนี้ตั้ง Lambda concurrency เป็น 0 และลบ API ของเดโม ไม่ลบ baseline/ชุด validator การหมดอายุเพียงอย่างเดียวไม่ได้ลบ resource; secret, logs, table และ storage อาจยังมีค่าใช้จ่าย ดูรายการ cleanup ใน [README_reference_deployment.md](README_reference_deployment.md#costs-and-shutdown)

### เปิดกลับหลัง stop

```powershell
aws sso login --profile minny --region ap-southeast-1
.venv/Scripts/python.exe tools/deploy_reference_demo.py --stage resume
```

เปิด URL ที่คำสั่ง `RESUMED` แสดง หรืออ่าน `url` ใน `reports/deployment/reference-demo-v1/deployment.json` **URL เดิมจะใช้ไม่ได้เมื่อ API ถูกลบ** และอาจขึ้น `{"message":"Not Found"}` คำสั่ง resume ใช้ฟังก์ชัน/รหัสเข้าเดิม ตรวจ code/config hash ก่อน สร้าง API เฉพาะเมื่อไม่พบ API ของเดโม และคืน Lambda concurrency ไม่ fit หรือ deploy โค้ดใหม่ ไม่รีเซ็ตโควตารายวันและไม่ต่อวันหมดอายุ หาก release หมดอายุจะหยุดให้ตรวจ release ใหม่ก่อน

อย่าใช้ `--stage deploy` แทน resume หลัง stop: เส้นทาง deploy เดิมไม่ได้ซ่อม API ID ที่ถูกลบหรือคืน concurrency ให้เอง

สำหรับการเปลี่ยน baseline ในอนาคต ให้เปลี่ยนผ่าน server configuration หลังตรวจ compatibility/hash/schema และ validation แล้วเท่านั้น ไม่สลับ artifact อัตโนมัติและไม่ต้องเขียน Dashboard ใหม่ สถานะ training และขั้นตอน resume อยู่ใน [README_reference_baseline.md](README_reference_baseline.md)
