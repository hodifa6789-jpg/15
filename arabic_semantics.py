"""Conservative, clause-scoped Arabic semantic backstop with exact source offsets.

This is not a general Arabic parser. Unrecognized LLM proposals are quarantined for
review, not allowed to determine severity. Normalization never changes audit text.
"""
from __future__ import annotations
from dataclasses import dataclass
import re


def normalize(text: str) -> str:
    return re.sub(r'[\u064b-\u065f\u0670ـ]', '', text).translate(str.maketrans('أإآى', 'اااي'))


@dataclass(frozen=True)
class Clause:
    text: str
    start: int
    end: int


def clauses(text: str) -> list[Clause]:
    # Adversatives terminate negation scope; an attached واو does not by itself.
    boundaries = list(re.finditer(r'[.!؟؛\n،,]+|(?:ولكن(?:ه|ها)?|لكنه|لكنها|لكن|بس)\s+|\s+و(?=(?:نشر|ضربني|اعتدى|دخل|اخترق|حولت(?:له)?|دفعت(?:له)?|هددني|أرسل|ارسل|بعتلي|حدثت|انتهى)\b)', text))
    out, start = [], 0
    for end, following in [(m.start(), m.end()) for m in boundaries] + [(len(text), len(text))]:
        raw = text[start:end]
        if raw.strip():
            lead = len(raw) - len(raw.lstrip())
            out.append(Clause(raw.strip(), start + lead, end - len(raw) + len(raw.rstrip())))
        start = following
    return out


PATTERNS = {
    'ordinary_dispute': r'خلاف|اختلفنا|نزاع|تخاصمنا|تخانقنا|سوء\s+تفاهم',
    'verbal_threat': r'(?:هددني|يهددني|هدد|يهدد|هددوني|تهديد)',
    'death_threat': r'(?:هددني\s+(?:بالقتل|يقتلني)|(?:رح|راح|سوف)\s+(?:اقتلك|يقتلني)|بقتلك|ساقتلك|تهديد\s+بالقتل)',
    'publication_threat': r'(?:هددني|يهددني|هدد|يهدد|تهديد).{0,65}(?:بنشر|ينشر|يفضح)|(?:رح|راح|سوف)\s+(?:ينشر|يفضح)(?:ها|هم|ني)?',
    'publication_occurred': r'(?<!ي)(?:نشر|نزل|اننشرت|نشرت|تم\s+نشر|يتم\s+نشر|ينشر)\s*(?:الصور|صوري|صورتي|المعلومات|الفيديو|معلوماتي|اي\s+شيء|اشي|شي)?',
    'physical_assault': r'(?:اعتدي\s+علي|اعتداء|ضربني|ضربوني|اذاني\s+جسديا)',
    'financial_fraud': r'(?:احتيال|نصب\s+علي|نصب\s+عليّ|خدعني|احتال\s+علي)',
    'unauthorized_debit': r'(?:انسحب|انخصم|انخصمت|سحبوا|خصموا|تم\s+سحب|خصم).{0,65}(?:حسابي|حساب\s+البنك|البنكي|بطاقتي)',
    'account_takeover': r'(?<!\w)و?(?:دخل\s+(?:(?:علي|الي)\s+)?حسابي|اخترق\s+حسابي|استولي\s+علي\s+حسابي|(?:سرق|انسرق)\s+حسابي|سرق\s+كلمة\s+السر)',
    'defamation': r'(?:نشر|ينشر).{0,30}(?:اكاذيب|معلومات\s+كاذبة|اشاعات)|شوه\s+سمعتي|تشهير',
    'harassment': r'(?:تحرش\s+بي|تحرش\s+في|يتحرش|تحرش|لاحقني|ضايقني|شتمني|اهانني|سبني)',
    'privacy_violation': r'(?:انتهك\s+خصوصيتي|صورني\s+دون\s+(?:علمي|اذني))',
    'extortion': r'(?:ابتزني|يبتزني|ابتزاز)',
    'reported_fear': r'(?:خايف|خائ[ففة]+|قلقان|قلقة|خوف\s+وقلق|خفت|بخاف)',
    'immediate_danger': r'(?:في\s+(?:اي\s+)?خطر(?:\s+مباشر)?|بخطر\s*(?:مباشر)?|خطر\s+(?:علي|حاليا)|مهدد\s+(?:هسا|حاليا)|واقف\s+عند\s+بابي|قدام\s+بيتي\s+هسا|معه\s+سلاح\s+قدامي|بحاول\s+يفوت\s+علي\s+هسا)',
    'payment_made': r'(?:دفعت(?:له|لها|لهم|ه)?|حولت(?:له|لها|لهم)?|ادفع|احول)\b',
    'financial_loss': r'(?:خسرت\s+(?:اي\s+)?(?:مصاري|مال|مبلغ)|خسارة(?:\s+مالية)?)',
    'injury': r'(?<!\w)(?:و?ب?ال)?(?:اصابة|اصبت|جروح|جرحني|اذي(?:\s+جسدي)?|ضرر(?:\s+جسدي)?)(?!\w)',
    'media_possession': r'(?:معه|عنده|يمتلك)\s+(?:الصور|صوري|المواد)|(?:بعتلي|بعثلي|بعت\s+لي|ارسل(?:\s+لي)?)\s+(?:نسخة\s+من\s+)?(?:احدي\s+)?(?:صوري|صورتي|صورة|صور|الصورة|الصور)',
    'evidence': r'(?:عندي|معي)\s+(?:كل\s+)?(?:محادثات|المحادثة|المحادثات|رسائل|الرسائل|شهود|صور\s+الشاشة)|(?:المحادثة|المحادثات|الرسائل)\s+(?:الواتساب\s+)?محفوظة',
    'time': r'\b(?:اليوم\s+(?:العصر|الصبح|صباحا|المسا|مساء|بالليل)|امس(?:\s+العصر)?|مبارح|قبل\s+يومين|بتاريخ\s+\d[\d/\-]*)\b',
    'threat_deadline': r'(?:خلال|مهلة(?:\s+الـ)?)\s*\d{1,3}\s*ساعة|(?:رح|راح|سوف)\s+ينشر(?:ها|هم)?\s+(?:الليلة|اليوم|هسا|هلا)',
    'recurrence': r'اول\s+مرة|تكرر|اكثر\s+من\s+مرة|عدة\s+مرات|مرتين|مرة\s+ثانية',
    'relationship': r'\b(?:جاري|زميلي|قريبي|شخص\s+بعرفه|اخي|صديقي)\b',
    'resolved': r'(?:انتهي|خلص)\s+(?:الخلاف|الموضوع)|انحلت\s+المشكلة|(?:التهديد\s+)?توقف',
    'desired_outcome': r'(?:بدي|اريد)\s+(?:بس\s+|فقط\s+)?(?:نصائح|نصيحة|ارشاد|اعرف\s+كيف|توثيق|اقدم\s+بلاغ|بلاغ)',
    'declines_draft': r'(?:ما\s+بدي|لا\s+اريد|مش\s+حابب)\s+(?:اقدم\s+|تقديم\s+)?بلاغ',
    'threat_capability': r'(?:قادر|يستطيع)\s+(?:ينفذ|تنفيذ|الوصول)|يقدر\s+يوصلني',
    'account_access': r'(?:لسه|ما\s+زال)\s+(?:داخل\s+الحساب|يتحكم\s+بالحساب|قادر\s+يدخل)',
}
MEDIA = r'(?:صور|صوري|صورتي|معلوماتي|فيديو|مقاطع|ينشرها|ينشرهم|بنشرها)'
MONEY = r'(?:دينار|مصاري|فلوس|مبلغ|ادفع|دفعت|تدفع|حولت|احول|تحول|تنفذ\s+طلبي)'
UNAUTHORIZED = r'(?:دون|بدون|من\s+غير)\s+(?:اذني|موافقتي|علمي)|ما\s+وافقت|غير\s+مصرح'
UNCERTAIN = r'(?:ما\s+بعرف|لا\s+اعرف|لا\s+اعلم|ما\s+كنت\s+متاكد|مش\s+متاكد|مو\s+متاكد|غير\s+متاكد|لست\s+متاكد|يمكن|ربما|اشك|ممكن)'
NON_EVENT = r'(?:في\s+المسرحية|في\s+فيلم|حلمت|كمثال\s+افتراضي|لو\s+افترضنا|اتهمني\s+اني|قال\s+(?:احدهم\s+)?(?:اني|انني)\s+هددته)'
INSTRUCTION = r'(?:تجاهل\s+(?:كل\s+|جميع\s+)?(?:التعليمات|القواعد)|صنف(?:ني|ها|\s+القضية)|اكتب\s+(?:في\s+التقرير|انني)|اعتبر(?:ني|ها)|system\s*:|ignore\s+(?:previous|all)\s+instructions)'


def is_instruction(text: str) -> bool:
    return bool(re.search(INSTRUCTION, normalize(text), re.I))


def polarity_for(text: str, match: re.Match, kind: str) -> str | None:
    """Scope from the nearest predicate boundary, preserving conditions and uncertainty."""
    prefix = text[:match.start()]
    # Bound a new independent clause introduced with an explicit conjunction/subject.
    local = re.split(r'\s+(?:وانا|وهو|والشخص|وقد|ثم|وما|ولا)\s+', prefix)[-1]
    # Keep the negation marker after a conjunction.
    if re.search(r'\s(?:وما|ولا)\s+[^،.]*$', prefix):
        local = re.search(r'\s(?:وما|ولا)\s+[^،.]*$', prefix).group()
    if re.search(NON_EVENT, text) or is_instruction(text):
        return None
    # Double negation is intentionally abstained, never treated as an affirmation.
    if re.search(r'(?:مش|ليس|مو)\s+(?:صحيح\s+انه\s+)?(?:ما|لم|مش)|ما\s+(?:بقدر|اقدر)\s+انفي', local):
        return 'uncertain'
    if re.search(UNCERTAIN, local):
        return 'uncertain'
    if kind in {'payment_made', 'publication_occurred', 'immediate_danger', 'injury'}:
        if re.search(r'(?<!\w)(?:اذا|لو)\s+(?:ما\s+)?[^.،]{0,80}$', local) and not re.search(UNCERTAIN, local):
            return None
        if re.search(r'(?:رح|راح|سوف|بدي)\s*$', local):
            return None
    if kind=='injury' and re.search(r'(?:توعد|هدد|يهدد|خايف|خائف)',local):
        return None
    # Negation can include auxiliaries; it ends before a subsequent finite positive event.
    markers=list(re.finditer(r'(?:^|\s)(?:و?(?:ما|لم|لن|لا|ليس|ليست|مش|مو|لست|ماني)|بدون|دون|نف[ىي])\s+',local))
    if markers and len(local)-markers[-1].end()<=65:
        between = local[markers[-1].end():]
        if not re.search(r'(?:هددني|ضربني|نشر|خسرت|دفعت|حولت|اعتدي|بعتلي|انتهي|صار\s+خلاف)', between):
            return 'denied'
    return 'reported'


def _offset_map(text: str) -> tuple[str, list[int]]:
    chars, offsets = [], []
    for i, char in enumerate(text):
        for n in normalize(char):
            chars.append(n)
            offsets.append(i)
    return ''.join(chars), offsets


def extract_rules(sources: dict[str, str]) -> list[dict]:
    output = []
    for source, original in sources.items():
        for clause in clauses(original):
            text, offsets = _offset_map(clause.text)
            if re.search(NON_EVENT, text) or is_instruction(text):
                continue
            hits = []
            for kind, pattern in PATTERNS.items():
                for match in re.finditer(pattern, text):
                    polarity = polarity_for(text, match, kind)
                    if polarity is None:
                        continue
                    if kind=='desired_outcome' and polarity!='reported':
                        continue
                    if kind=='verbal_threat' and match.group()=='تهديد' and not re.search(r'(?:يوجد|في|بدون|دون|اي)\s*$',text[:match.start()]):
                        continue
                    if kind == 'unauthorized_debit' and not re.search(UNAUTHORIZED, text):
                        continue
                    if kind == 'publication_threat' and not re.search(MEDIA, text):
                        continue
                    if kind == 'publication_occurred':
                        before = text[:match.start()]
                        if re.search(r'(?:هدد|يهدد|تهديد|رح|راح|سوف|خايف|خائف|قلقان|مقابل\s+عدم)', before):
                            continue
                        if not re.search(MEDIA, text) and polarity != 'denied':
                            continue
                        if re.search(r'(?:اكاذيب|معلومات\s+كاذبة|اشاعات)', text):
                            continue
                    if kind == 'immediate_danger' and polarity == 'reported':
                        if re.search(r'(?:كان|كنت|سابقا|زمان)', text[:match.start()]):
                            continue
                    if kind == 'payment_made' and polarity == 'reported' and re.search(r'(?:طلب|طالب|هدد|يهدد|اذا|مقابل|اتفقت)', text[:match.start()]):
                        continue
                    if kind == 'recurrence' and 'اول' in match.group():
                        polarity = 'denied'
                    if kind == 'declines_draft':
                        polarity = 'reported'
                    start = clause.start + offsets[match.start()]
                    end = clause.start + offsets[match.end()-1] + 1
                    temporal = 'past' if re.search(r'سابقا|زمان|كان\s+يهدد|قبل\s+هيك|قبل\s+هالرسالة|ما\s+كنت', text) else 'current' if re.search(r'حاليا|هسا|الان', text) else 'unspecified'
                    # Scope-bearing quotes for denials/uncertainty; snippets alone can be misleading.
                    if polarity != 'reported' or kind in {'extortion', 'publication_threat', 'death_threat', 'unauthorized_debit'}:
                        start, end = clause.start, clause.end
                    hits.append(dict(kind=kind, polarity=polarity, quote=original[start:end], source=source,
                        start=start, end=end, temporal_context=temporal, verification='unverified',
                        extraction_method='explicit_rule_candidate'))
            positive = {h['kind'] for h in hits if h['polarity']=='reported'}
            if 'publication_threat' in positive and re.search(MONEY, text) and re.search(r'(?:اذا\s+ما|و?الا|مقابل\s+عدم\s+النشر)', text):
                hits.append(dict(kind='extortion', polarity='reported', quote=clause.text, source=source,
                    start=clause.start, end=clause.end, temporal_context='unspecified', verification='unverified', extraction_method='explicit_rule_candidate'))
                positive.add('extortion')
            if positive & {'extortion', 'publication_threat'}:
                amount = re.search(r'\d{1,7}\s*دينار', text)
                if amount:
                    a, b = clause.start+offsets[amount.start()], clause.start+offsets[amount.end()-1]+1
                    hits.append(dict(kind='amount_demanded', polarity='reported', quote=original[a:b], source=source,
                        start=a, end=b, verification='unverified', extraction_method='explicit_rule_candidate', temporal_context='unspecified'))
            if positive & {'death_threat','publication_threat','extortion'}:
                hits = [h for h in hits if not (h['kind']=='verbal_threat' and h['polarity']=='reported')]
            output.extend(hits)
            # Explicit demand in its own clause; do not infer that a demand was paid.
            if re.search(r'(?:طلب\s+مني|طالبني|طلب\s+تحويل)',text):
                amount=re.search(r'\d{1,7}\s*دينار',text)
                if amount:
                    a,b=clause.start+offsets[amount.start()],clause.start+offsets[amount.end()-1]+1
                    output.append(dict(kind='amount_demanded',polarity='reported',quote=original[a:b],source=source,
                        start=a,end=b,verification='unverified',extraction_method='explicit_rule_candidate',temporal_context='unspecified'))
            for hit in hits:
                if hit['kind']=='media_possession' and hit['polarity']=='reported' and re.search(r'(?:بعتلي|ارسل|بعثلي)', normalize(hit['quote'])):
                    output.append(dict(hit,kind='evidence'))
    return output


def yes_no(text: str) -> str:
    value = normalize(text.strip().lower())
    if re.search(UNCERTAIN, value) or re.match(r'لا\s+(?:ادري|اعرف|اعلم)', value):
        return 'unknown'
    if re.match(r'^(?:لا|كلا|مش|مو|ما\s+صار|لم\s+يحدث)\b', value):
        return 'no'
    if re.match(r'^(?:نعم|اه|ايوه|اجل|صار|حصل)\b', value):
        return 'yes'
    return 'unknown'


def is_direct_danger(text: str) -> bool:
    return any(f['kind']=='immediate_danger' and f['polarity']=='reported' for f in extract_rules({'text':text}))
