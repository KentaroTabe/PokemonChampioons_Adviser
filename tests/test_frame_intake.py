"""フレーム受信の取りこぼし削減 (最新1枚保持+連続処理) の検証。

以前は処理中に届いたフレームを即破棄していたため、処理完了から次の
到着まで遊びが生じ実効レートが100msの倍数へ丸められていた。
最新1枚を保持して続けて処理する方式に変更した効果を確認する。

使い方: scripts/run_test.sh test_frame_intake
"""
import asyncio


def _setup():
    """server の1フレーム処理をモックに差し替える"""
    import server
    server.frame_counter = 0
    server.processed_counter = 0
    server.dropped_counter = 0
    server._busy = False
    server._pending_frame = None
    return server


def test_processes_pending_after_busy():
    """処理中に届いた最新フレームが、完了後に続けて処理される"""
    server = _setup()
    processed = []

    async def fake_one(sid, data):
        processed.append(data)
        await asyncio.sleep(0.02)

    server._handle_one_frame = fake_one

    async def scenario():
        # frame1 の処理中に frame2 が届く → 破棄せず続けて処理する
        task = asyncio.ensure_future(server.handle_frame("s", "frame1"))
        await asyncio.sleep(0.005)
        await server.handle_frame("s", "frame2")
        await task

    asyncio.get_event_loop().run_until_complete(scenario())
    assert processed == ["frame1", "frame2"], processed
    assert server.dropped_counter == 0, server.dropped_counter
    assert server.frame_counter == 2
    print("test_processes_pending_after_busy OK:", processed)


def test_only_latest_is_kept():
    """保持は最新1枚だけ (古いものは破棄カウント)"""
    server = _setup()
    processed = []

    async def fake_one(sid, data):
        processed.append(data)
        await asyncio.sleep(0.03)

    server._handle_one_frame = fake_one

    async def scenario():
        task = asyncio.ensure_future(server.handle_frame("s", "f1"))
        await asyncio.sleep(0.005)
        await server.handle_frame("s", "f2")   # 保持
        await server.handle_frame("s", "f3")   # f2を上書き → f2が破棄
        await task

    asyncio.get_event_loop().run_until_complete(scenario())
    assert processed == ["f1", "f3"], processed
    assert server.dropped_counter == 1, server.dropped_counter
    print("test_only_latest_is_kept OK:", processed)


def test_busy_released_on_error():
    """処理が例外を投げても _busy が解放される (詰まり防止)"""
    server = _setup()

    async def boom(sid, data):
        raise RuntimeError("boom")

    server._handle_one_frame = boom
    try:
        asyncio.get_event_loop().run_until_complete(
            server.handle_frame("s", "f1"))
    except RuntimeError:
        pass
    assert server._busy is False
    assert server._pending_frame is None
    print("test_busy_released_on_error OK")


def test_selection_advice_gate():
    """対戦中 (battle_active) は選出/準備画面の誤分類フレームで
    選出アドバイスを出さない (2026-08-18 第3回: turn進行中に5回混入)"""
    import server as srv
    ok = {"scene": "selection", "outcome": None, "battle_active": False}
    assert srv.should_advise_selection(ok) is True
    assert srv.should_advise_selection(dict(ok, scene="standby")) is True
    # 対戦中の誤分類フレーム → 出さない
    assert srv.should_advise_selection(dict(ok, battle_active=True)) is False
    # 勝敗表示が残っている間 → 出さない (前試合の相手を参照してしまう)
    assert srv.should_advise_selection(dict(ok, outcome="loss")) is False
    # 対戦シーンでは元々出さない
    assert srv.should_advise_selection(dict(ok, scene="command")) is False
    print("test_selection_advice_gate OK")


def main() -> None:
    test_processes_pending_after_busy()
    test_only_latest_is_kept()
    test_busy_released_on_error()
    test_selection_advice_gate()
    print("ALL OK")


if __name__ == "__main__":
    main()
