# Урок 47. tcpdump и Wireshark: читаем трафик

tcpdump был с нами почти в каждом уроке блока 4: им мы смотрели рукопожатие, повторы, окна и MSS. Пора разобраться с ним как с инструментом: где он видит пакеты, как отбирать нужные, как сохранять запись и разбирать её потом, чем ему помогает Wireshark. А заодно увидеть глазами наблюдателя, что раскрывает незашифрованный трафик и что остаётся видно, даже когда всё зашифровано.

## Анализаторы протоколов

Олифер (гл. 28, с. 886-888) называет такие программы **анализаторами протоколов**: они захватывают кадры, проходящие через сетевой интерфейс, и раскладывают их по уровням - Ethernet, IP, TCP, протокол приложения - с расшифровкой каждого поля. Самые распространённые сегодня:

- **tcpdump** - программа командной строки, есть почти на любом сервере. Захватывает пакеты через библиотеку libpcap, умеет печатать их кратко или подробно и записывать в файл;
- **Wireshark** - графический анализатор с разборщиками (dissectors) почти для любого протокола: показывает каждое поле, собирает поток TCP целиком, строит статистику;
- **tshark** - тот же Wireshark, но в командной строке: удобно на сервере и в скриптах.

Все они понимают общий формат записи **pcap** (и его развитие pcapng). Отсюда обычный порядок работы: на сервере, где нет графики, записать трафик `tcpdump -w файл.pcap`, а разбирать запись в Wireshark на своём компьютере.

## Где анализатор видит трафик

Анализатор видит только то, что проходит через интерфейс машины, на которой он запущен, и в той точке пути пакета через ядро, где стоит (урок 44): на приёме - до файервола, на отправке - после него и после дисциплины очереди.

По умолчанию сетевая карта отбрасывает кадры, адресованные чужому MAC-адресу. tcpdump включает **неразборчивый режим** (promiscuous mode, Олифер гл. 10, с. 313), в котором карта принимает все кадры, что до неё дошли (ключ `-p` это отключает). Но в сети на коммутаторах до порта доходят только кадры для этой машины, широковещательные, групповые и изредка кадры для адреса, которого коммутатор ещё не знает (их он рассылает всем, урок 16). Чтобы администратор мог видеть чужой трафик для диагностики, на коммутаторе настраивают **зеркалирование порта** (port mirroring, SPAN): копия всего трафика нужного порта отправляется на порт, где стоит анализатор (Олифер гл. 28, с. 887). Для той же цели ставят ответвители (TAP) прямо в кабель. А `tcpdump -i any` слушает сразу все интерфейсы машины - удобно, когда неясно, куда идёт пакет; заголовок Ethernet при этом заменяется служебным, а неразборчивый режим не включается.

## tcpdump: основные ключи

- `-i ИНТЕРФЕЙС` - где слушать; `-n` - не превращать адреса и номера портов в имена (иначе tcpdump сам отправляет запросы DNS и замедляется); часто пишут `-nn` по старой привычке, в современных версиях это то же самое;
- `-e` - показывать заголовок канального уровня (MAC-адреса), `-v`, `-vv` - подробности: поля заголовка IP и протоколов выше;
- `-c ЧИСЛО` - остановиться после стольких пакетов;
- `-w файл` - записать в файл pcap вместо печати, `-r файл` - прочитать запись;
- `-s ЧИСЛО` - сколько байт каждого пакета сохранять (snaplen); по умолчанию 262144 байта, то есть на практике весь пакет. Для разбора заголовков Ethernet, IP и TCP хватает примерно 100 байт (например, `-s 96`); при больших значениях в запись попадает начало данных: в нашем опыте уже при `-s 200` в файле оказался бы заголовок с паролем;
- `-A` и `-X` - показать содержимое пакета как текст или как байты и текст;
- `-tttt`, `-ttt` - время в полном формате или как интервал от предыдущего пакета;
- `--immediate-mode` и `-U` - отдавать и записывать пакеты сразу, без накопления в буфере.

При выходе tcpdump печатает сводку: сколько пакетов захвачено (`captured`), сколько прошло фильтр в ядре (`received by filter`, так этот счётчик понимается в Linux) и сколько ядро отбросило, потому что tcpdump не успевал забирать (`dropped by kernel`) - если это число не ноль, запись неполная.

## Фильтры захвата

Без фильтра на нагруженном сервере tcpdump утонет в трафике. Фильтр захвата пишется на языке **BPF** (Berkeley Packet Filter, фильтр пакетов Беркли):

- по адресам: `host 192.0.2.1`, `src host ...`, `dst net 198.51.100.0/24`;
- по протоколам и портам: `tcp`, `udp`, `icmp`, `port 443`, `dst port 53`, `portrange 6000-6010`;
- связки: `and`, `or`, `not` и скобки: `'tcp port 443 and not host 192.0.2.1'`;
- по отдельным байтам и битам заголовка: `'tcp[tcpflags] & tcp-syn != 0'` - пакеты с флагом SYN, как в наших прошлых опытах.

Важно, где фильтр работает: tcpdump **переводит его в маленькую программу** и отдаёт ядру. Ядро выполняет её для каждого пакета ещё до копирования в tcpdump, и в tcpdump попадают только подходящие пакеты. Поэтому даже узкий фильтр на очень нагруженной машине обходится дёшево. Увидеть эту программу позволяет `tcpdump -d ФИЛЬТР`.

## Wireshark и tshark: фильтры отображения

У Wireshark есть свой, второй язык фильтров - **фильтры отображения**. Они применяются к уже захваченным и разобранным пакетам и знают поля всех протоколов, которые умеет разбирать Wireshark:

| Задача | Фильтр захвата (BPF) | Фильтр отображения (Wireshark) |
|---|---|---|
| адрес | `host 192.0.2.1` | `ip.addr == 192.0.2.1` |
| порт TCP | `tcp port 443` | `tcp.port == 443` |
| только запросы HTTP | сложно (проверкой байтов) | `http.request` |
| Client Hello (начало TLS) | сложно | `tls.handshake.type == 1` |
| повторные передачи | - | `tcp.analysis.retransmission` |

Фильтр захвата решает, что вообще попадёт в запись; фильтр отображения - что показать из записи. Фильтры отображения намного богаче, но работают в программе, а не в ядре, поэтому на сервере обычно пишут широкую запись с простым фильтром захвата, а уточняют уже при разборе. Ещё две полезные возможности Wireshark: **Follow TCP Stream** - собрать данные соединения целиком, как их видела программа, и раздел **Statistics** - разговоры (Conversations), иерархия протоколов (Protocol Hierarchy), графики. В tshark то же доступно ключами: `-Y` - фильтр отображения, `-T fields -e поле` - вывод выбранных полей, `-z` - статистика.

## Как это увидеть в Linux

В двух пространствах имён: клиент `a` (`192.0.2.1`) и учебный сайт на `b` (`192.0.2.2`), который спрашивает пароль, ставит куки и показывает "баланс счёта" - по HTTP на порту 8080 и по HTTPS на порту 8443 с самоподписанным сертификатом. Посмотрим на ping с подробностями, на программу фильтра, запишем запрос по HTTP в файл и прочитаем его tcpdump и tshark, а потом повторим запрос по HTTPS. Пароль и куки - учебные, придуманы для опыта. Нужны Linux (подойдёт WSL2), права sudo, python3, tcpdump, tshark, curl и openssl (`sudo apt install python3 tcpdump tshark curl openssl`; установщик tshark спросит, разрешить ли захват обычным пользователям, - для опыта ответ не важен, можно оставить "нет"). Всё создаётся в пространствах имён `hbh-*` и в конце удаляется.

Создай файл `capture-lab.sh` и запусти `bash capture-lab.sh`:
```
set -u
for c in python3 tcpdump tshark curl openssl base64; do
  command -v $c >/dev/null || { echo "нужен $c: sudo apt install python3 tcpdump tshark curl openssl"; exit 1; }
done
# убрать остатки прошлого запуска
for n in hbh-a hbh-b; do
  sudo ip netns pids $n 2>/dev/null | xargs -r sudo kill
  sudo ip netns del $n 2>/dev/null
done
d=$(mktemp -d)
chmod 755 "$d"
# клиент a (192.0.2.1) и сервер b (192.0.2.2)
for n in hbh-a hbh-b; do sudo ip netns add $n; done
sudo ip link add eth0 netns hbh-a type veth peer name eth0 netns hbh-b
sudo ip netns exec hbh-a ip addr add 192.0.2.1/24 dev eth0
sudo ip netns exec hbh-b ip addr add 192.0.2.2/24 dev eth0
for n in hbh-a hbh-b; do
  sudo ip netns exec $n ip link set lo up
  sudo ip netns exec $n ip link set eth0 up
done
A="sudo ip netns exec hbh-a"
B="sudo ip netns exec hbh-b"
# учебный сайт на b: HTTP на 8080 и HTTPS на 8443, спрашивает пароль и ставит куки
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 1 \
  -subj /CN=hbh.test -keyout "$d/key.pem" -out "$d/cert.pem" 2>/dev/null
chmod 644 "$d/key.pem"
cat > "$d/site.py" <<'EOF'
import http.server, ssl, sys, threading
class Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<h1>Account of student</h1><p>balance: 1000</p>\n"
        self.send_response(200)
        self.send_header("Set-Cookie", "session=7f3a9c21e4b8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
def run(port, tls):
    srv = http.server.HTTPServer(("0.0.0.0", port), Site)
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(sys.argv[1] + "/cert.pem", sys.argv[1] + "/key.pem")
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.serve_forever()
threading.Thread(target=run, args=(8443, True), daemon=True).start()
run(8080, False)
EOF
$B python3 "$d/site.py" "$d" &
sleep 1
$A ping -c 1 -W 1 192.0.2.2 >/dev/null     # заранее узнать MAC соседа
echo '--- 1. tcpdump: one ping, with link layer (-e) and details (-v)'
$B timeout 3 tcpdump -i eth0 -n -e -v -c 2 icmp 2>/dev/null &
sleep 1
$A ping -c 1 -W 1 192.0.2.2 >/dev/null
wait $!
echo '--- 2. a capture filter runs in the kernel: the first lines of its program'
tcpdump -d 'tcp port 8080' | head -6
echo '--- 3. plain HTTP: write everything to a file, then read it'
$B tcpdump -i eth0 -n -s 0 --immediate-mode -U -w "$d/http.pcap" 'tcp port 8080' 2>"$d/http.err" &
tpid=$!
sleep 1
$A curl -s -o /dev/null -u student:lab-password-123 http://192.0.2.2:8080/account
sleep 0.5
sudo kill -INT $tpid; wait $tpid
tail -3 "$d/http.err"
echo 'the packets, briefly:'
tcpdump -r "$d/http.pcap" -n -tt 2>/dev/null | awk '{ $1 = ""; print }' | sed -E 's/^ IP //; s/, options \[[^]]*\]//; s/, win [0-9]+//' | head -14
echo '--- 4. what an observer on the path reads in plain HTTP'
tcpdump -r "$d/http.pcap" -n -A 2>/dev/null | grep -aoE 'GET /[^ ]* HTTP/1\.1|(Host|Authorization|Set-Cookie): [^\r]*|<h1>.*</p>' | awk '!seen[$0]++'
auth=$(tcpdump -r "$d/http.pcap" -n -A 2>/dev/null | grep -aoE 'Basic [A-Za-z0-9+/=]+' | head -1 | cut -d' ' -f2)
echo "Basic is not encryption, just base64: $(echo "$auth" | base64 -d)"
echo '--- 5. the same in tshark: a protocol dissector and display filters'
tshark -r "$d/http.pcap" -Y http.request -T fields -E header=y -e ip.src -e http.request.method -e http.request.uri -e http.authorization 2>/dev/null
tshark -r "$d/http.pcap" -Y http.response -T fields -E header=y -e http.response.code -e http.set_cookie 2>/dev/null
tshark -r "$d/http.pcap" -q -z io,phs 2>/dev/null | grep -E 'frames'
echo '--- 6. the same request over TLS'
$B tcpdump -i eth0 -n -s 0 --immediate-mode -U -w "$d/https.pcap" 'tcp port 8443' 2>/dev/null &
tpid=$!
sleep 1
$A curl -s -o /dev/null -k -u student:lab-password-123 --resolve hbh.test:8443:192.0.2.2 https://hbh.test:8443/account
sleep 0.5
sudo kill -INT $tpid; wait $tpid
for f in http https; do
  echo "$f.pcap: $(tcpdump -r "$d/$f.pcap" 2>/dev/null | wc -l) packets, password found $(tcpdump -r "$d/$f.pcap" -A 2>/dev/null | grep -ac 'Authorization: Basic') times, 'balance' found $(tcpdump -r "$d/$f.pcap" -A 2>/dev/null | grep -ac balance) times"
done
echo 'what tshark still sees in TLS:'
tshark -r "$d/https.pcap" -Y 'tls.handshake.type == 1' -T fields -E header=y -e ip.src -e ip.dst -e tcp.dstport -e tls.handshake.extensions_server_name 2>/dev/null
tshark -r "$d/https.pcap" -Y tls -T fields -e ip.src -e tcp.len -e _ws.col.Info 2>/dev/null
echo '--- cleanup'
for n in hbh-a hbh-b; do
  sudo ip netns pids $n 2>/dev/null | xargs -r sudo kill
  sudo ip netns del $n
done
rm -rf "$d"
ip netns list | grep -cE '^hbh-(a|b)( |$)'
```

Вот что получилось на нашем сервере:
```
--- 1. tcpdump: one ping, with link layer (-e) and details (-v)
06:22:18.804217 b2:db:f4:a3:7a:a9 > e6:4f:74:33:bb:77, ethertype IPv4 (0x0800), length 98: (tos 0x0, ttl 64, id 19493, offset 0, flags [DF], proto ICMP (1), length 84)
    192.0.2.1 > 192.0.2.2: ICMP echo request, id 5194, seq 1, length 64
06:22:18.804235 e6:4f:74:33:bb:77 > b2:db:f4:a3:7a:a9, ethertype IPv4 (0x0800), length 98: (tos 0x0, ttl 64, id 38927, offset 0, flags [none], proto ICMP (1), length 84)
    192.0.2.2 > 192.0.2.1: ICMP echo reply, id 5194, seq 1, length 64
--- 2. a capture filter runs in the kernel: the first lines of its program
(000) ldh      [12]
(001) jeq      #0x86dd          jt 2	jf 8
(002) ldb      [20]
(003) jeq      #0x6             jt 4	jf 19
(004) ldh      [54]
(005) jeq      #0x1f90          jt 18	jf 6
--- 3. plain HTTP: write everything to a file, then read it
10 packets captured
10 packets received by filter
0 packets dropped by kernel
the packets, briefly:
192.0.2.1.44514 > 192.0.2.2.8080: Flags [S], seq 3565036655, length 0
192.0.2.2.8080 > 192.0.2.1.44514: Flags [S.], seq 2077875059, ack 3565036656, length 0
192.0.2.1.44514 > 192.0.2.2.8080: Flags [.], ack 1, length 0
192.0.2.1.44514 > 192.0.2.2.8080: Flags [P.], seq 1:140, ack 1, length 139: HTTP: GET /account HTTP/1.1
192.0.2.2.8080 > 192.0.2.1.44514: Flags [.], ack 140, length 0
192.0.2.2.8080 > 192.0.2.1.44514: Flags [P.], seq 1:147, ack 140, length 146: HTTP: HTTP/1.0 200 OK
192.0.2.2.8080 > 192.0.2.1.44514: Flags [FP.], seq 147:195, ack 140, length 48: HTTP
192.0.2.1.44514 > 192.0.2.2.8080: Flags [.], ack 196, length 0
192.0.2.1.44514 > 192.0.2.2.8080: Flags [F.], seq 140, ack 196, length 0
192.0.2.2.8080 > 192.0.2.1.44514: Flags [.], ack 141, length 0
--- 4. what an observer on the path reads in plain HTTP
GET /account HTTP/1.1
Host: 192.0.2.2:8080
Authorization: Basic c3R1ZGVudDpsYWItcGFzc3dvcmQtMTIz
Set-Cookie: session=7f3a9c21e4b8
<h1>Account of student</h1><p>balance: 1000</p>
Basic is not encryption, just base64: student:lab-password-123
--- 5. the same in tshark: a protocol dissector and display filters
ip.src	http.request.method	http.request.uri	http.authorization
192.0.2.1	GET	/account	Basic c3R1ZGVudDpsYWItcGFzc3dvcmQtMTIz
http.response.code	http.set_cookie
200	session=7f3a9c21e4b8
eth                                      frames:10 bytes:1009
  ip                                     frames:10 bytes:1009
    tcp                                  frames:10 bytes:1009
      http                               frames:2 bytes:319
        tcp.segments                     frames:1 bytes:114
--- 6. the same request over TLS
http.pcap: 10 packets, password found 1 times, 'balance' found 1 times
https.pcap: 15 packets, password found 0 times, 'balance' found 0 times
what tshark still sees in TLS:
ip.src	ip.dst	tcp.dstport	tls.handshake.extensions_server_name
192.0.2.1	192.0.2.2	8443	hbh.test
192.0.2.1	517	Client Hello (SNI=hbh.test)
192.0.2.2	754	Server Hello, Change Cipher Spec, Application Data, Application Data, Application Data, Application Data
192.0.2.1	80	Change Cipher Spec, Application Data
192.0.2.1	160	Application Data
192.0.2.2	255	Application Data
192.0.2.2	493	Application Data, Application Data, Application Data
192.0.2.1	24	Application Data
--- cleanup
0
```

Разберём.

**Шаг 1: ping под увеличительным стеклом.** Ключ `-e` добавил MAC-адреса отправителя и получателя и тип содержимого кадра: `ethertype IPv4 (0x0800)` (урок 15). Ключ `-v` раскрыл заголовок IP (урок 25): `ttl 64`, идентификатор, флаг `DF` у запроса и `length 84` - 20 байт заголовка IP, 8 байт заголовка ICMP и 56 байт данных ping; вместе с 14 байтами Ethernet - те самые `length 98`. Ответ ядро `b` отправило без флага DF: `flags [none]`.

**Шаг 2: программа фильтра.** `tcp port 8080` превратился в программу для ядра. Первые строки: загрузить два байта со смещения 12 - тип содержимого кадра - и сравнить с `0x86dd` (IPv6); если это IPv6, взять байт 20 (номер следующего протокола в заголовке IPv6) и сравнить с 6 (TCP), затем взять два байта со смещения 54 - порт источника - и сравнить с `0x1f90`, то есть 8080. Дальше в программе (мы показали только начало) - те же проверки для IPv4 и для порта назначения. Именно такую программу ядро выполняет для каждого пакета.

**Шаг 3: запись в файл.** tcpdump записал 10 пакетов, ядро ничего не отбросило. Чтение записи (`-r`) показывает знакомую картину: рукопожатие, запрос `GET /account` на 139 байт, подтверждение, ответ с заголовками (146 байт) и телом (48 байт). Учебный сервер работает по HTTP/1.0 и закрывает соединение сразу после ответа. Тело (48 байт) ядро придержало: заголовки ещё не подтверждены, а мелкий сегмент при этом отправлять нельзя (алгоритм Нейгла, урок 39). Тут сервер закрыл соединение, ядро добавило FIN к придержанным данным и отправило их сразу: флаги `[FP.]`. В WSL2 подтверждение успело прийти раньше, тело и FIN ушли отдельно, и пакетов стало 13.

**Шаг 4: что видит наблюдатель в открытом HTTP.** `-A` показывает содержимое пакетов как текст, и в нём всё: адрес страницы, имя сайта из заголовка `Host` (у нас там адрес), заголовок `Authorization` с паролем, куки сеанса из ответа и сама страница с балансом. Заголовок `Authorization: Basic` - не шифрование: это просто логин и пароль в кодировке base64, и одна команда `base64 -d` возвращает `student:lab-password-123`. Куки сеанса не менее ценны: кто их получил, тот может выдать себя за пользователя, не зная пароля (Олифер гл. 30, с. 945, описывает и это, и повтор перехваченных запросов). Подробнее о том, что утекает в открытом HTTP, - в уроке 75.

**Шаг 5: то же в tshark.** Разборщик HTTP сам нашёл запрос и ответ и выдал нужные поля: метод, адрес, заголовок `Authorization`, код ответа и куки. Статистика иерархии протоколов (`-z io,phs`): 10 кадров Ethernet, IP и TCP, из них 2 кадра несут HTTP; строка `tcp.segments` значит, что ответ HTTP Wireshark собрал из нескольких сегментов TCP.

![Что видит наблюдатель](img/observer.png)

**Шаг 6: тот же запрос по TLS.** Пароль и баланс в записи HTTPS не найдены ни разу. Но кое-что наблюдатель видит и здесь:

- адреса и порты обеих сторон;
- **имя сайта** `hbh.test` - оно передаётся открыто (если не используется расширение ECH) в расширении SNI первого сообщения TLS, Client Hello, чтобы сервер знал, какой сертификат предъявить (подробно - в блоке про TLS);
- размеры и время: запрос клиента - одна запись TLS на 160 байт (138 байт HTTP - на байт меньше, чем в шаге 3: `Host: hbh.test:8443` короче, чем `Host: 192.0.2.2:8080`, - и 22 байта TLS: 5 байт заголовка записи, 1 байт типа содержимого и 16 байт кода проверки целостности). Сразу после рукопожатия сервер присылает билеты для возобновления сеанса (по 255 байт), а ответ - записи 168 и 70 байт (заголовки 146 и тело 48 плюс по 22 байта TLS) - ушёл в одном сегменте со вторым билетом, вместе 493 байта. Содержимое зашифровано, но его размер виден почти до байта, и по размерам и ритму обмена иногда можно догадаться, что происходит.

Любопытная деталь: в WSL2 Client Hello занял 1568 байт, а на сервере 517. В WSL2 OpenSSL 3.5 или новее: по умолчанию она кладёт в Client Hello ключ гибридного обмена X25519MLKEM768 (больше килобайта), устойчивого к квантовым компьютерам. Сервер там тоже его поддерживает и ответил своей половиной, поэтому и его первый ответ вырос с 754 до 1841 байта. В OpenSSL 3.0 на нашем сервере такого обмена нет. Разница меньше размера ключа, потому что старый Client Hello дополнялся служебным расширением padding до 517 байт.

В конце `0`: пространства имён удалены.

## Безопасность: пассивный перехват

Пассивный перехват - это просто чтение чужого трафика: он ничего не меняет в передаче, поэтому почти не оставляет следов и его трудно обнаружить (Олифер гл. 26, с. 812). Анализатор протоколов - инструмент администратора, но в чужих руках он становится средством шпионажа (там же, с. 814). Возможность прочитать трафик есть у всех, через кого он проходит: у соседей по открытой сети Wi-Fi, у владельца или взломщика маршрутизатора, у провайдера, у того, кто получил доступ к зеркальному порту коммутатора.

Что раскрывает **незашифрованный** трафик, мы видели в опыте: пароли, куки сеансов, адреса страниц, содержимое. Что остаётся видно в **зашифрованном**: кто, с кем, когда и сколько обменивается данными, имена сайтов в SNI (пока не используется ECH), запросы DNS, если они не зашифрованы. Олифер (гл. 28, с. 890) сравнивает такие метаданные с телефонным счётом: видно, с кем и сколько говорили, но не о чём. Даже этого часто достаточно, чтобы многое узнать о человеке или компании.

Как защищаться:

- **Шифровать всё.** HTTPS для любых сайтов, а не только для страниц с паролями, и HSTS, чтобы после первого посещения браузер не пытался зайти по HTTP. Открытые протоколы (HTTP с паролями, Telnet, FTP, почта без TLS) заменять защищёнными: SSH, SFTP, IMAP и SMTP с TLS.
- **Куки сеанса** - с флагом `Secure`: браузер передаёт их только по HTTPS и не отдаст в открытом запросе. (`HttpOnly` защищает от другого - от чтения куки скриптом страницы.)
- **В чужих сетях** - не доверять сети: пользоваться только зашифрованными соединениями, шифровать запросы DNS (DoH, DoT, урок 67); для доступа к рабочим ресурсам - VPN своей организации.
- **На своём оборудовании** - ограничивать доступ к настройкам зеркалирования и к сетевым шкафам, защищать порты коммутаторов (урок 16).
- **Свои записи трафика** - тоже секрет: файл pcap может содержать пароли и куки. Записывать только нужное (фильтр, `-s` под размер заголовков), хранить с ограниченным доступом, удалять после разбора. И записывать трафик только своих сетей или с разрешения владельца.

## Итог

- tcpdump, Wireshark и tshark - анализаторы протоколов: захватывают кадры и раскладывают их по уровням; общий формат записи - pcap.
- Анализатор видит то, что проходит через интерфейс его машины, в своей точке пути через ядро; чужой трафик в сети на коммутаторах доступен только через зеркалирование порта или ответвитель.
- Фильтр захвата (BPF) компилируется в программу и выполняется в ядре до копирования; фильтр отображения Wireshark работает с разобранными полями при анализе.
- В открытом трафике видно всё: пароли (Basic - лишь base64), куки, содержимое. В TLS видны адреса, порты, имя сайта в SNI, размеры и время.
- Защита от пассивного перехвата - шифрование всего трафика, флаги куки, осторожность в чужих сетях и бережное обращение со своими записями.

## Что почитать

- Олифер, гл. 28, с. 886-888: анализаторы протоколов, зеркалирование портов, Wireshark; с. 889-890: метаданные трафика (NetFlow). Гл. 26, с. 812 и 814: пассивные атаки и снифферы. Гл. 10, с. 313: неразборчивый режим.
- `man 8 tcpdump`, `man 7 pcap-filter` (язык фильтров захвата), `man 1 tshark`, `man 4 wireshark-filter` (фильтры отображения).
- Руководство пользователя Wireshark: wireshark.org/docs.
