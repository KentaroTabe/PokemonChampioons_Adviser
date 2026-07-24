"""分析・コーチングAPI (server.py の socket ハンドラ) の結線検証。

フロントエンドの「分析・コーチング」パネルが呼ぶハンドラを直接実行し、
analysis_result が正しい内容で emit されることを確認する。
軽量分析3種は常に検証し、実対戦を伴うプレイブック/改善は
ローカルShowdown (8100) が稼働している場合のみ検証する。

使い方: scripts/run_test.sh test_analysis_api
"""
import asyncio
import socket as _socket


def _showdown_up() -> bool:
    try:
        with _socket.create_connection(("127.0.0.1", 8100), timeout=1):
            return True
    except OSError:
        return False


def main() -> None:
    import server

    captured = []

    async def fake_emit(event, data=None, room=None):
        captured.append((event, data))

    server.sio.emit = fake_emit

    async def run_light():
        await server.run_analysis("t", {"kind": "battles", "last": 20})
        await server.run_analysis("t", {"kind": "meta"})
        await server.run_analysis("t", {"kind": "review"})

    asyncio.run(run_light())
    results = {d["kind"]: d["text"] for e, d in captured
               if e == "analysis_result"}
    assert "対戦ログ分析" in results.get("battles", ""), results.get("battles")
    assert "環境ダイジェスト" in results.get("meta", ""), results.get("meta")
    assert "レビュー" in results.get("review", ""), results.get("review")
    print("軽量分析3種 OK (敗因分析/環境ダイジェスト/レビュー)")

    if not _showdown_up():
        print("プレイブック/改善はSKIP (Showdown 8100 未稼働)")
        return

    captured.clear()

    async def run_heavy():
        await server.run_playbook("t", {"opponents": 2, "battles": 6})
        await server.improve_team("t", {"battles": 6, "max_changes": 1})

    asyncio.run(run_heavy())
    results = {d["kind"]: d["text"] for e, d in captured
               if e == "analysis_result"}
    n_progress = sum(1 for e, _ in captured if e == "analysis_progress")
    assert "プレイブック" in results.get("playbook", ""), \
        results.get("playbook")
    assert "改善候補" in results.get("improve", ""), results.get("improve")
    assert n_progress > 0, "進捗が配信されていない"
    print(f"プレイブック/改善 OK (進捗{n_progress}件)")
    print("ALL OK")


if __name__ == "__main__":
    main()
