# CEPT เริ่มต้น 3 ขั้น (OpenDSS, Windows)

> ร่าง D2 (developer-validated, ยังไม่ผ่าน new-user review ตาม §4), 2026-09-12

สิ่งที่ต้องมี: Python 3.10 + wheel CEPT ที่ติดตั้งแล้ว (`cept --version` ต้องตอบเลขเวอร์ชัน)

## ขั้น 1 — เตรียมเคส

```powershell
mkdir thq; cd thq
copy ..\first_circuit_case.json case.json
```

## ขั้น 2 — รัน load flow

```powershell
cept study run case.json --engine opendss --out run
```

ต้องเห็น `Solver validation: PASS` และบรรทัด `Report: ...\run\report.html`

## ขั้น 3 — ตรวจหลักฐาน + เปิดรายงาน

```powershell
cept study verify run
cept physics audit run
```

ต้องเห็น `"passed": true` ทั้งสองคำสั่ง แล้วเปิด `run\report.html` ดูผล

## ถ้าไม่ผ่าน

- `BLOCKED` + ชื่อฟิลด์ที่ขาด → เติมข้อมูลตามที่บอก ห้ามเดาค่าใส่เอง
- engine อื่นที่ไม่ใช่ `opendss` → รุ่นนี้รองรับ OpenDSS เท่านั้น
- ผลรันเก่าหลังแก้ input → รันใหม่เสมอ ห้ามอ้างผลเก่า

อ่านสถานะงานจาก `report.html` ตอนบน: วัตถุประสงค์ ผลทางวิศวกรรม เพดาน claim และข้อจำกัดที่ยังไม่รู้
