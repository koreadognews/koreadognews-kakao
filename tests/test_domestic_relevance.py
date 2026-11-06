"""Offline-only editorial screening tests; no user messages or network calls."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape
from feed_collector import classify_domestic, parse_feed
from rss_archive import _write_day, load_for_issue


class DomesticRelevanceTests(unittest.TestCase):
    def test_classification_examples(self):
        checks = [
            ('"분명 유골함까지 받았는데"…발견된 반려견 사체', '펫산업 관계자 반응', '한국경제 전체 추가시험', None),
            ('애견유치원 학대, 영국 독일 사례', '시장 분석 및 정부 제도 조사', '헤럴드경제 전체 추가시험', None),
            ('말없이 펼쳐지는 사랑의 세계…연극 멜로드라마 열차', '고양이가 나오는 연극 소개', '뉴시스 문화 추가시험', None),
            ('국산 라이신 수출 확대', '일반 사료 제조와 축산 농가', '뉴시스 산업', None),
            ('대구사이버대·한국동물보건학회, 반려동물산업 전문인력 양성 업무협약 체결', '', '헤럴드경제 전체 추가시험', 'domestic_industry'),
            ('반려동물 사료 제조공장 신규 투자', '제품 생산 확대', '뉴시스 산업', 'domestic_industry'),
            ('펫테크 스타트업, 반려동물 위치추적 제품 출시', '신기술 개발', '한국경제 전체 추가시험', 'domestic_industry'),
            ('반려견 입양 문화행사 열린다', '보호소 입양 안내', '뉴시스 문화 추가시험', 'domestic_supplement'),
            ('고양이와 함께 산책하는 문화 행사', '생활 문화', '뉴시스 지방', 'domestic_supplement'),
            ('정부, 펫푸드 수출 지원 정책 발표', '', '농림축산식품부 보도자료', 'domestic_industry'),
            ('우리지역 가을맞이 시민 소식', '반려동물 입양 문화행사가 예정', '서울시 내 손안에 서울', 'domestic_supplement'),
            ('새로운 시장 성장 지원 방안', '반려동물 용품 수출 지원', '농림축산식품부 보도자료', 'domestic_industry'),
            ('Pet food', '반려동물 사료 산업', 'Example News', 'domestic_industry'),
        ]
        for title, summary, publisher, expected in checks:
            with self.subTest(title=title):
                self.assertEqual(classify_domestic(title, summary, publisher), expected)

    def test_rss_and_archived_items_share_screening(self):
        tz = ZoneInfo('Asia/Seoul')
        start = dt.datetime(2026, 11, 6, 6, tzinfo=tz)
        end = dt.datetime(2026, 11, 7, 6, tzinfo=tz)
        titles = [
            '반려동물 사료 제조기업 투자 확대',
            '반려견 입양 문화행사 개최',
            '반려견 사체 발견, 경찰 조사',
            '연극 멜로드라마 열차 개막',
        ]
        summaries = ['기업 투자 확대', '동물 보호', '펫산업 관계자 반응', '고양이 등장']
        feed={'publisher':'뉴시스 문화 추가시험','category':'domestic','hosts':['www.newsis.com']}
        xml='<rss><channel>' + ''.join(
            '<item><title>{}</title><link>https://www.newsis.com/view/{}</link>'
            '<pubDate>Fri, 06 Nov 2026 12:00:00 +0900</pubDate>'
            '<description>{}</description></item>'.format(escape(t),i,escape(s))
            for i,(t,s) in enumerate(zip(titles,summaries))
        ) + '</channel></rss>'
        rows=parse_feed(xml.encode('utf-8'),feed,start,end)
        self.assertEqual([x[0] for x in rows],['domestic_industry','domestic_supplement'])
        with tempfile.TemporaryDirectory() as temp:
            saved=[]
            for i,(title,summary) in enumerate(zip(titles,summaries)):
                saved.append({'title':title,'summary':summary,
                              'url':'https://www.newsis.com/view/'+str(i),
                              'publisher':feed['publisher'],'published_at':'2026-11-06T12:00:00+09:00',
                              'category':'domestic_industry'})
            _write_day(Path(temp),dt.date(2026,11,6),saved)
            replay=load_for_issue(dt.date(2026,11,7),archive_dir=Path(temp))
            self.assertEqual(len(replay['domestic_industry']),1)
            self.assertEqual(len(replay['domestic_supplement']),1)
            self.assertIn('사료',replay['domestic_industry'][0]['title'])
            self.assertIn('입양',replay['domestic_supplement'][0]['title'])

if __name__=='__main__':
    unittest.main()
