"""스킬 정규화 사전 정본 — `자바`/`JAVA`/`Java 11`을 하나로 묶는다.

**이 파일이 없으면 요건 통계와 갭 분석이 전부 쓰레기가 된다.** 공고는 같은 기술을 서로
다른 문자열로 쓴다(`자바`, `JAVA`, `Java8`, `JDK`). 정규화하지 않으면 "Java 요구 38%"가
"자바 12% + JAVA 15% + Java8 11%"로 흩어져 아무 결론도 나오지 않는다.

설계 원칙 셋.

1. **정본 이름(canonical)은 영문 표기 하나로 고정한다.** 노션 multi_select 옵션과 통계
   집계 키가 되므로, 한 기술에 이름이 둘이면 옵션이 둘 생긴다(`config/rules.py`의
   `ALL_SIGNAL_NAMES`가 같은 이유로 정본인 것과 같은 논리).

2. **자격증은 스킬이 아니다.** SQLD를 가진 것과 SQL을 실무로 쓴 것은 다르다. 그래서
   자격증은 `CERTS`에 따로 두고, `CERT_EVIDENCE`로 **"어느 스킬을 부분적으로 증빙하는가"**
   만 연결한다. 자격증이 스킬 레벨을 채워 주지는 않는다 — 채워 준다고 하면 갭 분석이
   거짓말을 한다.

3. **긴 별칭을 먼저 매칭한다.** `Spring Boot`와 `Spring`이 둘 다 사전에 있으면 긴 쪽이
   먼저 걸려야 한다. 안 그러면 `스프링부트`가 `Spring`으로만 잡히고 부트가 사라진다.
   `iter_aliases()`가 길이 내림차순을 보장한다.

사전을 늘리는 방법: 실제 공고에서 못 잡힌 단어를 `--probe-detail` 결과에서 보고 별칭에
추가한다. **추측으로 미리 채우지 말 것** — 안 쓰이는 별칭은 오탐만 늘린다.
"""
from __future__ import annotations

import re

# --- 정본 스킬 -> 별칭 목록 -------------------------------------------------
# 별칭에 정본 자신을 넣지 않아도 된다(자동 포함). 대소문자는 무시한다.
SKILLS: dict[str, list[str]] = {
    # 언어
    "Java": ["자바", "JDK", "Java8", "Java11", "Java17", "자바8", "자바11"],
    "Kotlin": ["코틀린"],
    "Python": ["파이썬"],
    "C": ["C언어", "씨언어"],
    "C++": ["씨플플", "CPP"],
    "C#": ["씨샵", "CSharp"],
    "JavaScript": ["자바스크립트", "JS", "ES6", "ECMAScript"],
    # ⚠️ `TS`를 뺐다 — 국내 공고에서 TS는 기술지원(Technical Support)을 뜻하는 경우가 많다.
    "TypeScript": ["타입스크립트"],
    "SQL": ["에스큐엘", "쿼리작성", "쿼리 작성"],

    # 백엔드 프레임워크
    # ⚠️ 별칭에서 `Boot`를 뺐다 — 부트캠프·부트스트랩에 걸린다. 정본과 나머지 별칭으로 충분하다.
    "Spring Boot": ["스프링부트", "스프링 부트", "SpringBoot"],
    "Spring": ["스프링", "Spring Framework", "스프링 프레임워크"],
    "Spring Security": ["스프링시큐리티", "스프링 시큐리티"],
    "JPA": ["Hibernate", "하이버네이트", "Spring Data JPA", "제이피에이"],
    "MyBatis": ["마이바티스", "iBatis", "아이바티스"],
    "JSP": ["Servlet", "서블릿"],
    "Node.js": ["노드", "NodeJS", "Node", "Express", "익스프레스"],
    "Django": ["장고"],
    "FastAPI": ["패스트API"],
    "Flask": ["플라스크"],

    # 프론트
    "React": ["리액트", "React.js", "ReactJS"],
    # ⚠️ 별칭에서 `뷰`를 뺐다 — **`코드리뷰`의 '뷰'에 걸린다.** 실제 백엔드 공고
    #    "코드리뷰 문화가 있습니다"가 Vue 요구로 잡혀 갭 목록에 프론트 프레임워크가
    #    끼는 사고가 났다(2026-09-30). 한글 1~2자 별칭은 단어 경계를 걸 수 없으므로
    #    부분 일치 사고가 나기 쉽다 — 짧은 한글 별칭은 넣지 말 것.
    "Vue": ["Vue.js", "VueJS"],
    "Next.js": ["넥스트", "NextJS"],
    "HTML/CSS": ["HTML", "CSS", "퍼블리싱", "마크업"],

    # DB
    "MySQL": ["마이에스큐엘"],
    "MariaDB": ["마리아디비"],
    "Oracle": ["오라클"],
    "PostgreSQL": ["포스트그레", "Postgres", "포스트그레SQL"],
    "MSSQL": ["SQL Server", "SQLServer"],
    "Redis": ["레디스"],
    "MongoDB": ["몽고디비", "몽고", "Mongo"],

    # 인프라 · 운영
    "Linux": ["리눅스", "Unix", "유닉스", "CentOS", "Ubuntu", "우분투", "RHEL"],
    "Docker": ["도커", "컨테이너화"],
    "Kubernetes": ["쿠버네티스", "k8s", "쿠버"],
    "AWS": ["아마존웹서비스", "Amazon Web Services"],
    "EC2": [],
    "S3": [],
    "RDS": [],
    "Nginx": ["엔진엑스"],
    "Tomcat": ["톰캣"],
    "Jenkins": ["젠킨스"],
    "CI/CD": ["CICD", "지속적통합", "지속적 통합", "GitHub Actions", "깃허브액션"],
    "Kafka": ["카프카"],
    "RabbitMQ": ["메시지큐", "Message Queue"],

    # 협업 · 도구
    # `형상관리`를 Git 별칭으로 둔 이유: 공고가 그 단어로만 쓰는 경우가 많다. 다만
    # 형상관리는 이제 전제조건이라 변별력이 낮다 — 신호로서의 가치는 아래 `Git 협업`이다.
    "Git": ["깃", "GitHub", "깃허브", "GitLab", "깃랩", "형상관리", "SVN"],
    "Git 협업": ["코드리뷰", "코드 리뷰", "Pull Request", "풀리퀘스트", "PR 기반",
                "브랜치 전략", "브랜치전략", "Git Flow", "컨벤션", "협업 경험"],
    "Gradle": ["그래들"],
    "Maven": ["메이븐"],
    "Jira": ["지라", "Confluence", "컨플루언스"],
    "Swagger": ["스웨거", "OpenAPI"],

    # 설계 · 개념
    "REST API": ["RESTful", "레스트", "API 설계", "API설계", "RestAPI"],
    "JUnit": ["단위테스트", "단위 테스트", "유닛테스트", "테스트코드", "테스트 코드",
              "TDD"],
    "객체지향": ["OOP", "객체 지향", "객체지향설계"],
    "자료구조/알고리즘": ["알고리즘", "자료구조", "코딩테스트", "코테"],
    "디자인패턴": ["Design Pattern", "디자인 패턴"],
    "MSA": ["마이크로서비스", "Microservice", "마이크로 서비스"],
    "JWT": ["OAuth", "토큰 인증", "인증/인가"],
}

# --- 자격증 정본 -> 별칭 ----------------------------------------------------
CERTS: dict[str, list[str]] = {
    "정보처리기사": ["정처기"],
    "정보처리산업기사": ["정처산기", "정보처리 산업기사"],
    "SQLD": ["SQL 개발자", "SQL개발자"],
    "SQLP": ["SQL 전문가"],
    "리눅스마스터 1급": ["리눅스마스터1급", "리마1급"],
    "리눅스마스터 2급": ["리눅스마스터2급", "리마2급", "리눅스 마스터 2급"],
    "AWS CLF": ["Cloud Practitioner", "클라우드 프랙티셔너"],
    "AWS AIF": ["AI Practitioner", "AWS AI Practitioner"],
    "AWS SAA": ["Solutions Architect Associate", "솔루션스 아키텍트"],
    "네트워크관리사": ["네트워크관리사 2급"],
    "OCJP": ["Oracle Certified Java Programmer"],
}

# --- 자격증이 부분 증빙하는 스킬 --------------------------------------------
# **자격증은 스킬 레벨을 채워 주지 않는다.** 이 표는 매처가 "요구: SQL / 보유: SQLD
# (자격증만)"처럼 **부분 충족**을 표시하기 위한 것이다. 자격증을 실무 경험으로 환산하면
# 갭 분석이 거짓말을 하고, 그러면 도구의 존재 이유가 사라진다.
CERT_EVIDENCE: dict[str, list[str]] = {
    "정보처리기사": ["자료구조/알고리즘", "SQL", "객체지향"],
    "정보처리산업기사": ["자료구조/알고리즘", "SQL"],
    "SQLD": ["SQL"],
    "SQLP": ["SQL"],
    "리눅스마스터 1급": ["Linux"],
    "리눅스마스터 2급": ["Linux"],
    "AWS CLF": ["AWS"],
    "AWS AIF": ["AWS"],
    "AWS SAA": ["AWS", "EC2", "S3", "RDS"],
    "네트워크관리사": [],
    "OCJP": ["Java"],
}


# --- 함의 관계 -------------------------------------------------------------
# `A를 요구하면 B도 요구한다`. 공고가 둘 다 적어 주지 않기 때문에 필요하다 — `Spring Boot
# 경험자`를 찾는 공고가 `Java`를 따로 적지 않는다고 자바가 필요 없는 게 아니다.
#
# **의도적으로 좁게 유지한다.** 넓히면 모든 공고가 불가능해 보이고, 그러면 적합도가
# 변별력을 잃는다. 논쟁 없이 참인 것만 넣는다(프레임워크→언어, DBMS→SQL).
# 별칭 정규식이 `MySQL` 안의 `SQL`을 일부러 안 잡기 때문에(단어 경계) 이 표가 그 자리를 메운다.
IMPLIES: dict[str, list[str]] = {
    "Spring Boot": ["Spring", "Java"],
    "Spring": ["Java"],
    "Spring Security": ["Spring", "Java"],
    "JPA": ["Java", "SQL"],
    "MyBatis": ["Java", "SQL"],
    "JSP": ["Java"],
    "MySQL": ["SQL"],
    "MariaDB": ["SQL"],
    "Oracle": ["SQL"],
    "PostgreSQL": ["SQL"],
    "MSSQL": ["SQL"],
    "React": ["JavaScript"],
    "Vue": ["JavaScript"],
    "Next.js": ["React", "JavaScript"],
    "TypeScript": ["JavaScript"],
    "Django": ["Python"],
    "FastAPI": ["Python"],
    "Flask": ["Python"],
    "Kubernetes": ["Docker"],
    "EC2": ["AWS"],
    "S3": ["AWS"],
    "RDS": ["AWS"],
    "Git 협업": ["Git"],
}


def implied(skill: str) -> list[str]:
    """`skill`이 함의하는 스킬들(재귀 전개, 자신은 제외)."""
    out: list[str] = []
    stack = list(IMPLIES.get(skill, []))
    while stack:
        item = stack.pop(0)
        if item == skill or item in out:
            continue
        out.append(item)
        stack.extend(IMPLIES.get(item, []))
    return out


def _alias_pattern(alias: str) -> re.Pattern[str]:
    r"""별칭 → 정규식.

    영문/숫자로만 된 별칭은 **단어 경계**를 요구한다. 안 그러면 `C`가 모든 영문 단어에
    걸리고 `S3`가 `S30`에 걸린다. 한글이 섞인 별칭은 `\b`가 한글 경계에서 동작하지
    않으므로 그대로 찾는다(한글은 영문처럼 부분 일치 사고가 드물다).

    `C++`/`C#`처럼 기호가 붙은 것은 이스케이프 후 뒤쪽 경계만 뺀다 — `+`가 단어 문자가
    아니라 `\b`를 붙이면 매칭되지 않는다.
    """
    escaped = re.escape(alias)
    ascii_only = alias.isascii()
    if ascii_only and alias.isalnum():
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
    if ascii_only:
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


def _build(table: dict[str, list[str]]) -> list[tuple[re.Pattern[str], str]]:
    """(패턴, 정본) 목록을 **별칭 길이 내림차순**으로 만든다.

    긴 것이 먼저 걸려야 `스프링부트`가 `Spring`으로 흡수되지 않는다.
    """
    pairs: list[tuple[str, str]] = []
    for canonical, aliases in table.items():
        for alias in {canonical, *aliases}:
            pairs.append((alias, canonical))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return [(_alias_pattern(alias), canonical) for alias, canonical in pairs]


_SKILL_PATTERNS = _build(SKILLS)
_CERT_PATTERNS = _build(CERTS)

ALL_SKILLS: list[str] = list(SKILLS.keys())
ALL_CERTS: list[str] = list(CERTS.keys())


def find_skills(text: str) -> list[str]:
    """텍스트에서 발견된 정본 스킬. 선언 순서로 정렬해 출력이 안정적이게 한다."""
    if not text:
        return []
    found = {canonical for pattern, canonical in _SKILL_PATTERNS if pattern.search(text)}
    return [s for s in ALL_SKILLS if s in found]


def find_certs(text: str) -> list[str]:
    """텍스트에서 발견된 정본 자격증."""
    if not text:
        return []
    found = {canonical for pattern, canonical in _CERT_PATTERNS if pattern.search(text)}
    return [c for c in ALL_CERTS if c in found]


def certified_skills(certs: list[str]) -> set[str]:
    """보유 자격증이 **부분 증빙**하는 스킬 집합. 레벨을 채우지는 않는다."""
    out: set[str] = set()
    for cert in certs:
        out.update(CERT_EVIDENCE.get(cert, []))
    return out
