import time
from typing import Callable

import pygame


class DualSenseController:
    def __init__(self, screenshot_button: int) -> None:
        pygame.init()
        pygame.joystick.init()
        if pygame.joystick.get_count() == 0:
            raise RuntimeError("没有检测到手柄")
        self.joystick = pygame.joystick.Joystick(0)
        self.joystick.init()
        self.screenshot_button = screenshot_button

    def loop(self, on_screenshot: Callable[[], None], cooldown_seconds: float = 1.0) -> None:
        print(f"检测到手柄: {self.joystick.get_name()}")
        print("按下指定按钮截图，Ctrl+C 可退出。")
        last_time = 0.0
        try:
            while True:
                for event in pygame.event.get():
                    if event.type == pygame.JOYBUTTONDOWN and event.button == self.screenshot_button:
                        now = time.time()
                        if now - last_time >= cooldown_seconds:
                            last_time = now
                            on_screenshot()
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("已退出。")
        finally:
            pygame.quit()

    def buttons_test(self) -> None:
        print("按下 DS5 上的按钮，我会告诉你按的是哪个编号")
        try:
            while True:
                pygame.event.pump()
                for i in range(self.joystick.get_numbuttons()):
                    if self.joystick.get_button(i):
                        print(f"按钮 {i} 被按下")
                pygame.time.wait(100)
        except KeyboardInterrupt:
            pass
        finally:
            pygame.quit()


