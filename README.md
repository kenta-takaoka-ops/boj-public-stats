# 公開金融統計の自動取得

日本銀行・財務省などの公的機関が公開している統計を、GitHub Actions で平日2回取得して `data/` に保存するだけのリポジトリです。
個人情報・独自の分析は一切含みません（取得するのは誰でもダウンロードできる公開データのみ）。
APIキーはこのリポジトリのどのファイルにも書きません。e-Stat などキーが必要な取得先は、GitHub の Secrets に登録したキーを実行時だけ使います（ログ・保存データには残しません）。

- `fetch.py` … 取得スクリプト（Python標準ライブラリのみ）
- `codes/boj.json` `codes/boj_derived.json` … 日本銀行 時系列統計データ検索サイトの系列コード
- `codes/extra.json` … 追加で取る公開ファイル（失敗したら manifest の errors）
- `codes/optional.json` … 海外の公的統計など（世界銀行・IMF・Eurostat・OECD・BIS・ECB・FRB）。週1回取得し、失敗しても warnings にとどめる
- `codes/keyed.json` … キーが必要な取得先。URL中の `{ESTAT_APP_ID}` などを Secrets の値に置き換えて取得する
- `data/manifest.json` … 最終取得時刻、エラー、警告

## 出所

データはすべて作成元（公表元）から直接取得しています。データの再配布を禁じている集約サービス（FRED など）は使いません。

| フォルダ | 作成元 | 主な取得先 |
|---|---|---|
| `boj/` | 日本銀行 | 時系列統計データ検索サイト API、公表予定（tkohyos.xlsx） |
| `mof/` | 財務省 | 国債金利情報、国債の入札結果 |
| `estat/` | 総務省統計局ほか | 政府統計の総合窓口（e-Stat）API |
| `worldbank/` | World Bank | World Development Indicators API（CC BY 4.0） |
| `imf/` | IMF | DataMapper API |
| `eurostat/` | Eurostat | Eurostat API |
| `oecd/` | OECD | OECD Data Explorer（SDMX API） |
| `bis/` | BIS / バーゼル銀行監督委員会 | BIS Data Portal API、CCyB 決定一覧 |
| `ecb/` | 欧州中央銀行 | ECB Data Portal API |
| `fed/` | 米連邦準備制度理事会 | Data Download Program（H.4.1・H.8・H.15・SLOOS） |

このサービスは、日本銀行「時系列統計データ検索サイト」のAPI機能を使用しています。サービスの内容は日本銀行によって保証されたものではありません。

このサービスは、政府統計総合窓口（e-Stat）のAPI機能を使用していますが、サービスの内容は国によって保証されたものではありません。

各統計の著作権・利用条件は各提供元に従います。
