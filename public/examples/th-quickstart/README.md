# CEPT เริ่มต้น 3 ขั้น (OpenDSS)

สองเส้นทางใช้คนละชุดคำสั่ง: **Public wheel / Colab** เป็นแพ็กเกจ OpenDSS ขนาดเล็ก ส่วน **Full Windows preview** เป็นแอปเดสก์ท็อปแบบเต็มที่ติดตั้งบน Windows และมีคำสั่งรายงานเพิ่มเติม

## Public wheel / Colab

ต้องมี Python 3.10 ขึ้นไปและ wheel สาธารณะที่ตรวจ SHA-256 แล้ว (ใน Colab ให้เปิด notebook จากลิงก์ Colab โดยตรง)

### ขั้นที่ 1 — เตรียมเคส

```powershell
mkdir thq; cd thq
copy ..\first_circuit_case.json case.json
```

### ขั้นที่ 2 — รันและตรวจหลักฐานด้วย Public CLI

```powershell
cept study run case.json --out run --format text
cept study verify run --format text
```

คำสั่งนี้เป็นชุด Public OpenDSS เท่านั้น ไม่มีคำสั่ง `physics audit` และไม่มี `report open`; ผลที่ต้องตรวจคือ `passed: true` จาก `study verify` และไฟล์ที่บันทึกใน `run` เท่านั้น

## Full Windows preview

หลังติดตั้ง Windows preview ตาม [Download](https://sarutesri.github.io/cept-studio/download/):

```powershell
cept study run case.json --engine opendss --out run --force
cept study verify run
cept report open run
```

Full preview มีคำสั่งนี้ทั้งหมด; อย่านำคำสั่งแบบเต็มไปใส่ใน Public wheel หรือ Colab เพราะ Public grammar ไม่รองรับ

## ถ้าไม่ผ่าน

- `BLOCKED` พร้อมชื่อฟิลด์ที่ขาด → เติมข้อมูลตามแหล่งจริง ห้ามเดาค่าใส่เอง
- engine อื่นที่ไม่ใช่ `opendss` → รุ่น Public นี้รองรับ OpenDSS เท่านั้น
- ผลรันเก่าหลังแก้ input → เลือก `--out` ใหม่หรือใช้ `--force` ใน Full preview แล้วรันใหม่เสมอ

Public สอนเส้นทาง Case → run → verify และไม่ได้อ้างว่ามี physics audit, SLD viewer, หรือ report server ในแพ็กเกจนั้น ส่วน Full preview อาจเปิด report จาก `cept report open run`
