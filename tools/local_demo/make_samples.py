"""Generate realistic test documents for the document centre and finance import."""
import random, csv, os
from datetime import date, timedelta
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import docx, openpyxl
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'samples')
F = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
FB = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

# 1. digital PDF invoice
c = canvas.Canvas(f'{OUT}/invoice_sportsmans.pdf', pagesize=A4)
y = 800
for line, bold in [("Sportsman's Warehouse Soweto", True), ('Maponya Mall, Soweto, 1809', False), ('', False),
                   ('TAX INVOICE', True), ('Invoice No: INV-20431', False), ('Invoice Date: 14/10/2026', False),
                   ('Due Date: 28/10/2026', False), ('Bill To: Dynasty Football Club', False), ('', False),
                   ('Description: 20 x Size 5 match balls, 40 training bibs', False), ('', False),
                   ('Subtotal          R 4 200.00', False), ('VAT (15%)         R 630.00', False),
                   ('Total Due         R 4 830.00', True), ('', False), ('Banking details: FNB 62812345678', False)]:
    c.setFont('Helvetica-Bold' if bold else 'Helvetica', 12); c.drawString(60, y, line); y -= 20
c.save()

# 2. photographed receipt (image -> OCR)
img = Image.new('RGB', (620, 860), (247, 244, 236)); d = ImageDraw.Draw(img)
f, fb = ImageFont.truetype(F, 24), ImageFont.truetype(FB, 28)
lines = [('SPAR KLIPTOWN', fb), ('Shop 4, Kliptown Square', f), ('RECEIPT', fb), ('Receipt No: 778812', f),
         ('Date: 2026-10-18', f), ('', f), ('Water 5L x6        R 119.94', f), ('Oranges 3kg        R 54.99', f),
         ('Energy bars x24    R 215.00', f), ('', f), ('TOTAL PAID         R 389.93', fb), ('Card payment APPROVED', f),
         ('Thank you for your purchase', f)]
yy = 40
for t, fo in lines:
    d.text((40, yy), t, fill=(30, 30, 30), font=fo); yy += 50
img = img.rotate(1.2, expand=True, fillcolor=(247, 244, 236)).filter(ImageFilter.GaussianBlur(0.6))
img.save(f'{OUT}/receipt_spar_photo.jpg', quality=82)

# 3. scanned medical certificate (image-only PDF -> OCR path)
img = Image.new('RGB', (1240, 1754), 'white'); d = ImageDraw.Draw(img)
f, fb = ImageFont.truetype(F, 34), ImageFont.truetype(FB, 44)
yy = 120
for t, fo in [('Dr N. Khumalo – General Practitioner', fb), ('Soweto Family Clinic, Practice No 0458921', f), ('', f),
              ('MEDICAL CERTIFICATE', fb), ('', f), ('Date: 15 October 2026', f), ('Patient name: Ayanda Zulu', f), ('', f),
              ('This is to certify that the above patient was examined', f), ('for an ankle injury and is NOT fit to play', f),
              ('contact sport until further review.', f), ('Valid until: 30 November 2026', f), ('', f),
              ('Signed: Dr N. Khumalo', f)]:
    d.text((110, yy), t, fill='black', font=fo); yy += 70
img.save(f'{OUT}/medical_cert_scan.pdf', 'PDF', resolution=150)

# 4. DOCX registration form (new player)
doc = docx.Document()
doc.add_heading('Dynasty FC – Player Registration Form', 1)
for k, v in [('First name', 'Lindiwe'), ('Surname', 'Mthembu'), ('Date of birth', '2012-03-09'),
             ('MYSAFA ID', 'GP-55821'), ('Team', 'Diamond Divas U15'), ('Position', 'Central Midfielder'),
             ('School', 'Orlando West High'), ('Parent/Guardian name', 'Zodwa Mthembu'),
             ('Guardian email', 'zodwa.mthembu@example.com'), ('Guardian phone', '082 555 0142')]:
    doc.add_paragraph(f'{k}: {v}')
doc.save(f'{OUT}/registration_lindiwe.docx')

# 5. DOCX consent form for an existing player
doc = docx.Document()
doc.add_heading('Parent / Guardian Consent and Indemnity', 1)
doc.add_paragraph("Player name: Mia Lee")
doc.add_paragraph("Parent/Guardian name: Grace Lee")
doc.add_paragraph("I hereby give my consent for my child to take part in Dynasty FC training, matches and travel, and grant permission for photographs to be used by the club.")
doc.add_paragraph("Signed date: 2 October 2026")
doc.save(f'{OUT}/consent_mia_lee.docx')

# 6. bank export CSV (FNB-style, with preamble lines, one duplicate, one bad row)
today = date.today()
rows = [
    (today - timedelta(days=20), 'PAYFAST DONATION T MOKOENA', '', '500.00'),
    (today - timedelta(days=18), 'SOWETO STARS PITCH HIRE', '1,200.00', ''),
    (today - timedelta(days=15), 'ENGEN KLIPTOWN FUEL', '650.50', ''),
    (today - timedelta(days=12), 'SPONSOR BUILDCO MONTHLY', '', '7,500.00'),
    (today - timedelta(days=12), 'SPONSOR BUILDCO MONTHLY', '', '7,500.00'),
    (today - timedelta(days=9), 'SAFA AFFILIATION FEE U15', '2,300.00', ''),
    (today - timedelta(days=7), 'MONTHLY ACCOUNT FEE', '95.00', ''),
    ('not a date', 'BROKEN ROW', '10.00', ''),
    (today - timedelta(days=3), 'PLAYER FEES OCTOBER', '', '3,150.00'),
]
with open(f'{OUT}/fnb_export.csv', 'w', newline='') as fh:
    w = csv.writer(fh)
    w.writerow(['Account', 'Dynasty FC Cheque 6281****5678']); w.writerow(['Statement period', 'Oct 2026']); w.writerow([])
    w.writerow(['Date', 'Description', 'Debit', 'Credit', 'Balance'])
    bal = 10000.0
    for d_, desc, deb, cre in rows:
        w.writerow([d_.strftime('%d/%m/%Y') if isinstance(d_, date) else d_, desc, deb, cre, f'{bal:.2f}'])

# 7. Excel ledger with a single signed Amount column (overlaps CSV -> duplicates across imports)
wb = openpyxl.Workbook(); ws = wb.active
ws.append(['Transaction Date', 'Details', 'Amount', 'Reference'])
ws.append([today - timedelta(days=18), 'SOWETO STARS PITCH HIRE', -1200.00, 'EFT-1'])
ws.append([today - timedelta(days=5), 'TOTALSPORTS KIT ORDER', -3890.00, 'EFT-2'])
ws.append([today - timedelta(days=2), 'DONATION K NAIDOO', 1000.00, 'EFT-3'])
wb.save(f'{OUT}/club_ledger.xlsx')
print(sorted(os.listdir(OUT)))
