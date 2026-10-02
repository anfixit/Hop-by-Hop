# Урок 75. HTTP/1.1: запросы, ответы, заголовки

Начинаем блок 9 - HTTP и TLS. Почти всё, что мы видим в браузере, приходит по **HTTP**, а многие современные прокси-протоколы (блоки 10-11) маскируются под HTTPS. Чтобы понять, что прячет TLS и что остаётся видно, сначала разберём сам HTTP: из чего состоят запрос и ответ, какие бывают методы, коды и заголовки и как одно TCP-соединение обслуживает много запросов. А в разделе безопасности посмотрим, что именно утекает, если HTTP идёт открытым текстом.

## URL и модель запрос-ответ

Адрес ресурса - **URL** - состоит из схемы (`http`, `https`), имени сервера (а при необходимости и порта), пути (Таненбаум, разд. 7.3, с. 729; Олифер, гл. 24, с. 769) и, возможно, строки параметров после `?` (RFC 3986). Как понимать путь, решает сервер: он не обязан совпадать с файлами на диске.

HTTP устроен как **запрос-ответ** поверх TCP: клиент открывает соединение (по умолчанию на порт 80, для HTTPS - 443), отправляет запрос и получает ответ (Таненбаум, с. 729-730, 741; Олифер, с. 770-771). Сервер не обязан помнить предыдущие запросы: каждый обрабатывается отдельно (протокол "без состояния"). HTTP/1.1 - **текстовый** протокол: стартовая строка и заголовки - строки ASCII, каждая оканчивается парой символов CR LF; тело может быть любым, в том числе двоичным.

## Формат сообщений

Запрос и ответ устроены одинаково (Олифер, с. 772-773, табл. 24.1):

- **стартовая строка**: в запросе - `метод путь версия` (`GET /index.html HTTP/1.1`), в ответе - `версия код фраза` (`HTTP/1.1 200 OK`);
- **заголовки** вида `Имя: значение`, по одному на строку, в любом порядке;
- **пустая строка** - граница заголовков;
- **тело** (необязательное): страница, файл, данные формы.

Сколько байт занимает тело, получатель узнаёт из заголовка `Content-Length` или, если длина заранее неизвестна, из **передачи частями** (`Transfer-Encoding: chunked`): каждая часть предваряется своей длиной в шестнадцатеричном виде, конец - часть нулевой длины. Если нет ни того, ни другого, ответ кончается закрытием соединения - и его уже нельзя переиспользовать. Исключения - ответы, у которых тела не бывает: на HEAD, коды 1xx, 204 и 304 (как в шаге 5).

## Методы

Основные методы (Таненбаум, с. 742-743, илл. 7.25):

- **GET** - получить ресурс; **HEAD** - то же, но только заголовки, без тела;
- **POST** - передать данные для обработки (форма, загрузка);
- **PUT** - записать ресурс по адресу, **DELETE** - удалить;
- **OPTIONS** - узнать, что можно делать с ресурсом; **CONNECT** - попросить прокси открыть туннель (урок о прокси - в блоке 10).

Стандарт (RFC 9110) делит методы на **безопасные** - по смыслу только читающие: клиент не просит ничего менять на сервере (GET, HEAD, OPTIONS, а также TRACE) - и **идемпотентные** - повтор которых даёт тот же результат, что и одно выполнение (безопасные плюс PUT и DELETE). POST ни то, ни другое: поэтому браузер переспрашивает, прежде чем повторно отправить форму. Олифер (с. 773) делит методы на "безопасные" и "опасные" по тому, передают ли они данные на сервер, - это не совпадает с терминами стандарта.

## Коды состояния

Код ответа - три цифры, первая задаёт класс (Таненбаум, с. 743, илл. 7.26; Олифер, с. 773-774):

- **1xx** - промежуточный ответ (`100 Continue`);
- **2xx** - успех (`200 OK`, `204 No Content`);
- **3xx** - перенаправление (`301 Moved Permanently` с новым адресом в заголовке `Location`, `304 Not Modified` - копия в кэше ещё годится);
- **4xx** - ошибка клиента (`400 Bad Request`, `401 Unauthorized` - нужна аутентификация, `403 Forbidden`, `404 Not Found`);
- **5xx** - ошибка сервера (`500 Internal Server Error`, `503 Service Unavailable`).

## Важные заголовки

(Таненбаум, с. 744-746, илл. 7.27; Connection и WWW-Authenticate - Олифер, табл. 24.1, с. 774)

- **Host** - имя сайта. На одном IP-адресе работают тысячи сайтов, и на уровне HTTP только этот заголовок говорит серверу, какой из них нужен, поэтому в HTTP/1.1 он обязателен;
- **User-Agent**, **Accept**, **Accept-Language** - кто спрашивает и что он готов принять; **Referer** - с какой страницы (по какому URL) пришёл пользователь;
- **Content-Type**, **Content-Length**, **Content-Encoding** - что в теле, какой длины, чем сжато;
- **Cache-Control**, **ETag**, **Last-Modified**, **If-None-Match**, **If-Modified-Since** - кэширование;
- **Set-Cookie** и **Cookie** - сеансы;
- **Authorization** и **WWW-Authenticate** - аутентификация;
- **Connection** - управление соединением (`keep-alive`, `close`).

## Постоянные соединения

В HTTP/1.0 на каждый объект открывалось новое TCP-соединение. HTTP/1.1 по умолчанию держит соединение **открытым** и передаёт по нему запрос за запросом: не нужно заново проходить рукопожатие и медленный старт TCP (урок 41) (Таненбаум, с. 748-750, илл. 7.29; Олифер, с. 771). Простаивающее соединение закрывают по таймауту. Стандарт разрешает и **конвейер** - отправку следующих запросов, не дожидаясь ответов, - но на практике его почти не используют: ответы должны идти строго по порядку, и один медленный блокирует остальные. На уровне HTTP эту проблему решил HTTP/2, а блокировку на уровне TCP - HTTP/3 (урок 76).

## Кэширование и cookies

**Кэш** (в браузере, в прокси) хранит ответы и отдаёт их повторно, пока они свежие: срок задаёт `Cache-Control: max-age` или более старый `Expires` (max-age важнее, RFC 9111). Когда срок вышел, кэш задаёт **условный запрос**: "дай ресурс, если он изменился" - с заголовком `If-None-Match` (версия ресурса из `ETag`) или `If-Modified-Since`. Если не изменился, сервер отвечает `304 Not Modified` без тела (Таненбаум, с. 746-748, илл. 7.28).

**Cookie** - способ добавить состояние в протокол без состояния: сервер присылает `Set-Cookie: имя=значение`, браузер запоминает и прикладывает `Cookie: имя=значение` к следующим запросам к этому сайту (Таненбаум, с. 745). Так работают входы на сайты, корзины - и слежка за пользователями (с. 753).

![Запрос и ответ HTTP/1.1](img/http.png)

## Как это увидеть в Linux

Напишем на Python маленький сервер HTTP/1.1 и запустим его в пространстве имён `hbh-web` на `127.0.0.1:8080`: страница с `ETag`, ответ частями, вход с cookie и страница, требующая пароль (`student` / `demo123`). Отправим запрос руками через `/dev/tcp` в bash, чтобы увидеть сырые байты, затем поработаем через `curl`, а в конце посмотрим через `tcpdump`, что видно в открытом HTTP. Нужны Linux (подойдёт WSL2), права sudo, python3, curl и tcpdump (`sudo apt install python3 curl tcpdump`); всё создаётся в пространстве имён и во временном каталоге и удаляется в конце.

Создай файл `http.sh` и запусти `bash http.sh`:
```
set -u
for c in python3 curl tcpdump; do
  command -v "$c" >/dev/null || { echo "нужны python3, curl и tcpdump: sudo apt install python3 curl tcpdump"; exit 1; }
done
ns=hbh-web
sudo ip netns pids $ns 2>/dev/null | xargs -r sudo kill
sudo ip netns del $ns 2>/dev/null
d=$(mktemp -d); chmod 755 "$d"
sudo ip netns add $ns
N="sudo ip netns exec $ns"
$N ip link set lo up
# tcpdump в Ubuntu ограничен профилем AppArmor: записывает только файлы *.pcap
# учебный сервер HTTP/1.1: страница с ETag, ответ частями, cookie и страница с паролем
cat > "$d/srv.py" <<'EOF'
import base64, hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
PAGE = b"<html><body>Hello, Hop-by-Hop!</body></html>\n"
ETAG = '"' + hashlib.sha256(PAGE).hexdigest()[:16] + '"'
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def version_string(self): return "LabServer/1.0"
    def log_message(self, *a): pass
    def send(self, code, body=b"", headers=()):
        self.send_response(code)
        for k, v in headers: self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(body)
    def do_HEAD(self): self.do_GET()
    def do_GET(self):
        if self.request_version == "HTTP/1.1" and "Host" not in self.headers:
            return self.send(400, b"Host header is required in HTTP/1.1\n")
        path = self.path.split("?")[0]
        if path == "/":
            if self.headers.get("If-None-Match") == ETAG:
                self.send_response(304); self.send_header("ETag", ETAG); self.send_header("Cache-Control", "max-age=60"); self.end_headers(); return
            return self.send(200, PAGE, [("Content-Type", "text/html"), ("ETag", ETAG), ("Cache-Control", "max-age=60")])
        if path == "/stream":
            self.send_response(200); self.send_header("Content-Type", "text/plain")
            self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
            for part in (b"first part\n", b"second, longer part\n"):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
            self.wfile.write(b"0\r\n\r\n"); return
        if path == "/login":
            return self.send(200, b"welcome\n", [("Set-Cookie", "session=abc123; Path=/")])
        if path == "/cart":
            return self.send(200, ("your cookie: %s\n" % self.headers.get("Cookie")).encode())
        if path == "/secret":
            if self.headers.get("Authorization") != "Basic " + base64.b64encode(b"student:demo123").decode():
                return self.send(401, b"who are you?\n", [("WWW-Authenticate", 'Basic realm="lab"')])
            return self.send(200, b"the secret page\n")
        self.send(404, b"not found\n")
HTTPServer(("127.0.0.1", 8080), H).serve_forever()
EOF
$N python3 "$d/srv.py" &
sleep 1
C="$N curl -s"
echo '--- 1. a request by hand: text lines ending with CR LF, an empty line, the body'
$N bash -c 'exec 3<>/dev/tcp/127.0.0.1/8080; printf "GET / HTTP/1.1\r\nHost: lab\r\nConnection: close\r\n\r\n" >&3; cat <&3' | sed -E 's/^Date: [^\r]*/Date: (now)/' | cat -A | sed 's/^/  /'
echo '--- 2. HTTP/1.1 requires Host'
$N bash -c 'exec 3<>/dev/tcp/127.0.0.1/8080; printf "GET / HTTP/1.1\r\nConnection: close\r\n\r\n" >&3; head -1 <&3' | sed 's/^/  /'
echo '--- 3. status codes and HEAD'
for u in / /nothing /secret; do printf '  %-9s %s\n' "$u" "$($C -o /dev/null -w '%{http_code}' http://127.0.0.1:8080$u)"; done
echo "  HEAD /: $($C -I http://127.0.0.1:8080/ | tr -d '\r' | grep -E '^(HTTP|Content-Length)' | tr '\n' ' ')"
echo '--- 4. two requests over one TCP connection (keep-alive)'
$C -o /dev/null -o /dev/null -w '  %{url_effective}: new TCP connections %{num_connects}, client port %{local_port}\n' http://127.0.0.1:8080/ http://127.0.0.1:8080/nothing
echo '--- 5. a conditional request: is the cached copy still valid?'
etag=$($C -I http://127.0.0.1:8080/ | tr -d '\r' | awk '/^ETag/ {print $2}')
echo "  ETag: $etag"
echo "  GET with If-None-Match: $($C -o /dev/null -w '%{http_code}, body %{size_download} bytes' -H "If-None-Match: $etag" http://127.0.0.1:8080/)"
echo '--- 6. a body of unknown length: chunked'
$C --raw http://127.0.0.1:8080/stream | cat -A | sed 's/^/  /'
echo '--- 7. cookies: the server sets one, the client sends it back'
$C -c "$d/jar" -o /dev/null -D - http://127.0.0.1:8080/login | grep -i '^Set-Cookie' | tr -d '\r' | sed 's/^/  response: /'
echo "  next request: $($C -b "$d/jar" http://127.0.0.1:8080/cart)"
echo '--- 8. what anyone on the path sees in plain HTTP'
$N tcpdump -i lo -nn -U --immediate-mode -w "$d/cap.pcap" 'tcp port 8080' 2>/dev/null &
tp=$!; sleep 1
$C -u student:demo123 -b "session=abc123" "http://127.0.0.1:8080/secret?account=42" -o /dev/null
sleep 1; sudo kill $tp; sleep 0.5
$N tcpdump -r "$d/cap.pcap" -nn -A 2>/dev/null | grep -aoE '(GET [^ ]+ HTTP/1.1|Authorization: Basic [A-Za-z0-9+/=]+|Cookie: [^ ]+|HTTP/1.1 [0-9]{3} [A-Za-z]+|the secret page)' | awk '!seen[$0]++' | sed 's/^/  /'
auth=$($N tcpdump -r "$d/cap.pcap" -nn -A 2>/dev/null | grep -aoE 'Basic [A-Za-z0-9+/=]+' | head -1 | cut -d' ' -f2)
echo "  base64 -d of the Authorization header: $(echo "$auth" | base64 -d)"
echo '--- cleanup'
sudo ip netns pids $ns 2>/dev/null | xargs -r sudo kill
sleep 0.3
sudo ip netns del $ns
rm -rf "$d"
ip netns list | grep -c "^$ns"
```

Вот что получилось на нашем сервере (порт клиента при каждом запуске свой):
```
--- 1. a request by hand: text lines ending with CR LF, an empty line, the body
  HTTP/1.1 200 OK^M$
  Server: LabServer/1.0^M$
  Date: (now)^M$
  Content-Type: text/html^M$
  ETag: "ab623d4329ecb188"^M$
  Cache-Control: max-age=60^M$
  Content-Length: 45^M$
  ^M$
  <html><body>Hello, Hop-by-Hop!</body></html>$
--- 2. HTTP/1.1 requires Host
  HTTP/1.1 400 Bad Request
--- 3. status codes and HEAD
  /         200
  /nothing  404
  /secret   401
  HEAD /: HTTP/1.1 200 OK Content-Length: 45 
--- 4. two requests over one TCP connection (keep-alive)
  http://127.0.0.1:8080/: new TCP connections 1, client port 40482
  http://127.0.0.1:8080/nothing: new TCP connections 0, client port 40482
--- 5. a conditional request: is the cached copy still valid?
  ETag: "ab623d4329ecb188"
  GET with If-None-Match: 304, body 0 bytes
--- 6. a body of unknown length: chunked
  b^M$
  first part$
  ^M$
  14^M$
  second, longer part$
  ^M$
  0^M$
  ^M$
--- 7. cookies: the server sets one, the client sends it back
  response: Set-Cookie: session=abc123; Path=/
  next request: your cookie: session=abc123
--- 8. what anyone on the path sees in plain HTTP
  GET /secret?account=42 HTTP/1.1
  Authorization: Basic c3R1ZGVudDpkZW1vMTIz
  Cookie: session=abc123
  HTTP/1.1 200 OK
  the secret page
  base64 -d of the Authorization header: student:demo123
--- cleanup
0
```

Разберём.

**Шаг 1: сырой ответ.** `cat -A` показывает невидимые символы: `^M$` - это CR LF в конце каждой строки. Стартовая строка `HTTP/1.1 200 OK`, заголовки, **пустая строка** (`^M$` без текста) и тело. `Content-Length: 45` - ровно длина тела: так клиент понимает, где ответ кончается, не закрывая соединения. (Дату мы заменили на `(now)`, чтобы вывод не менялся от запуска к запуску.)

**Шаг 2: Host.** Запрос HTTP/1.1 без заголовка `Host` - `400 Bad Request`: без него сервер не знает, какой из сайтов на этом адресе нужен. (В нашем учебном сервере это правило проверено явно; так ведут себя и настоящие веб-серверы.)

**Шаг 3: коды и HEAD.** `/` - 200, несуществующий путь - 404, страница с паролем без пароля - 401. `HEAD` вернул тот же код и тот же `Content-Length: 45`, что и `GET`; тела в ответе на HEAD по стандарту нет.

**Шаг 4: одно соединение.** `curl` выполнил два запроса подряд: для первого открыл одно новое TCP-соединение, для второго - ни одного, и порт клиента тот же: второй запрос ушёл по уже открытому соединению.

**Шаг 5: условный запрос.** Сервер выдал версию страницы в `ETag`. Запрос с `If-None-Match` и этой версией получил `304` и 0 байт тела: копия у клиента ещё годится, пересылать страницу не нужно.

**Шаг 6: передача частями.** Ответ без `Content-Length`: `b` (11) байт `first part\n`, затем `14` (20) байт второй части и завершающий `0`. Так передают данные, длина которых заранее неизвестна, - например, генерируемые на лету.

**Шаг 7: cookies.** Сервер прислал `Set-Cookie: session=abc123`, `curl` сохранил его в файл и при следующем запросе вернул в заголовке `Cookie` - сервер увидел свой cookie.

**Шаг 8: что видно на пути.** `tcpdump` записал запрос к странице с паролем, и в записи открытым текстом: путь вместе с параметрами (`/secret?account=42`), cookie сеанса, заголовок `Authorization: Basic ...` и сам ответ. `Basic` - это не шифрование, а кодирование base64: одна команда `base64 -d` дала `student:demo123`.

В конце `0`: пространство имён удалено.

## Безопасность: что утекает в открытом HTTP

Принцип: **в открытом HTTP любой узел на пути - Wi-Fi, провайдер, прокси - видит и может изменить всё**: адрес и параметры запроса, заголовки, cookies сеансов, пароли, содержимое страниц и форм. Украденный cookie сеанса позволяет войти под чужим именем без пароля, а изменённая страница может принести вредоносный код. Basic-аутентификация без шифрования - это пароль открытым текстом.

Как защищаться:

- **использовать только HTTPS** - HTTP внутри TLS (уроки 77-83): шифрование и проверка подлинности сервера; на сервере - перенаправлять HTTP на HTTPS;
- **включать HSTS** (`Strict-Transport-Security`): получив заголовок по HTTPS, браузер запомнит, что к сайту можно обращаться только по HTTPS, и дальше сам будет менять `http://` на `https://`, не отправляя запросов открыто; чтобы защитить и самый первый визит, домен вносят в список предзагрузки HSTS в браузерах;
- **помечать cookies** атрибутами `Secure` (только по HTTPS), `HttpOnly` (недоступен скриптам страницы) и `SameSite` (ограничивает отправку при запросах с чужих сайтов);
- **не класть секреты в URL**: адреса с параметрами оседают в журналах серверов и прокси, истории браузера и заголовке `Referer`;
- **не использовать Basic-аутентификацию без TLS** и вообще не передавать пароли открытым текстом (урок 74).

## Итог

- HTTP - текстовый протокол запрос-ответ поверх TCP: стартовая строка, заголовки, пустая строка, тело; строки оканчиваются CR LF.
- Методы GET, HEAD, POST, PUT, DELETE, OPTIONS, CONNECT; безопасные и идемпотентные методы по RFC 9110.
- Коды состояния делятся на классы 1xx-5xx; важные - 200, 301, 304, 400, 401, 404, 500.
- Host обязателен в HTTP/1.1; длину тела задаёт Content-Length или передача частями.
- HTTP/1.1 держит соединение открытым; кэш использует условные запросы и 304; cookies добавляют состояние.
- Открытый HTTP раскрывает всё; защита - HTTPS, HSTS, флаги cookies.

## Что почитать

- Таненбаум, разд. 7.3, с. 725-758, особенно 7.3.4, с. 740-753, и 7.3.5 о слежке, с. 753-758: HTTP, методы, коды, заголовки, кэширование, постоянные соединения. Ссылки на RFC 2616 и RFC 2109/2965 устарели: действуют RFC 9110-9112 и RFC 6265; HTTP/1.1 появился в 1997-1999 годах, а не в 2007-м.
- Олифер, гл. 24, с. 769-774: служба WWW, протокол HTTP, формат сообщений, коды. Утверждение, что HTTP/1.1 по умолчанию работает конвейером, неверно: конвейер разрешён, но почти не используется; код 401 по стандарту называется Unauthorized, а HTTP/3 стандартизован (RFC 9114, 2022).
- RFC 9110 (семантика HTTP), RFC 9111 (кэширование), RFC 9112 (HTTP/1.1), RFC 6265 (cookies; SameSite - в его обновлении, черновике 6265bis), RFC 6797 (HSTS), RFC 7617 (Basic-аутентификация).
