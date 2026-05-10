import math
import time
import multiprocessing as mp
import queue


class _MatplotlibRenderer:
    """
    真正的绘图引擎：运行在独立的子进程中。
    它不关心算法的进度，只负责接收数据并画图。
    """

    def __init__(self, parser):
        try:
            import matplotlib.pyplot as plt
            from matplotlib import colors as mcolors
            from matplotlib.patches import Rectangle
        except ImportError as exc:
            raise RuntimeError(
                "Training visualization requires matplotlib. Install it with: pip install matplotlib"
            ) from exc

        self.plt = plt
        self.mcolors = mcolors
        self.Rectangle = Rectangle

        self.parser = parser
        self.room_ids = list(parser.room_ids)
        self.room_to_index = {room_id: idx for idx, room_id in enumerate(self.room_ids)}
        self.half_periods_per_day = parser.periods * 2
        self.course_colors = self._build_course_colors()
        self.figure = None
        self.axes = []
        self._initialize_figure()

    def _initialize_figure(self):
        self.plt.ion()
        room_count = max(1, len(self.room_ids))
        cols = math.ceil(math.sqrt(room_count))
        rows = math.ceil(room_count / cols)
        self.figure, axes = self.plt.subplots(rows, cols, figsize=(cols * 4.8, rows * 3.8))

        if hasattr(axes, "flatten"):
            axes_list = list(axes.flatten())
        elif isinstance(axes, (list, tuple)):
            axes_list = list(axes)
        else:
            axes_list = [axes]

        self.axes = axes_list
        for idx, axis in enumerate(self.axes):
            if idx >= len(self.room_ids):
                axis.set_visible(False)

        self.figure.tight_layout(rect=(0, 0, 1, 0.95))
        # 捕获窗口关闭事件
        self.figure.canvas.mpl_connect('close_event', self.on_close)
        self.is_open = True

    def on_close(self, event):
        self.is_open = False

    def _build_course_colors(self):
        course_ids = list(self.parser.courses.keys())
        if not course_ids:
            return {}
        colors = {}
        for idx, course_id in enumerate(course_ids):
            hue = (idx * 0.61803398875) % 1.0
            rgb = self.mcolors.hsv_to_rgb((hue, 0.28, 0.96))
            colors[course_id] = rgb
        return colors

    @staticmethod
    def _week_label(week_signal):
        return {0: "", 1: "单", 2: "双"}.get(week_signal, "")

    def _format_gene_label(self, gene):
        course_id = gene['course_id']
        week_label = self._week_label(gene.get('week_signal', 0))
        if week_label:
            return f"{course_id}\n{week_label}周"
        return course_id

    def _configure_axis(self, axis, room_id):
        axis.clear()
        axis.set_xlim(0, self.parser.days)
        axis.set_ylim(self.half_periods_per_day, 0)
        axis.set_xticks([day + 0.5 for day in range(self.parser.days)])
        axis.set_xticklabels([f"Day {day + 1}" for day in range(self.parser.days)], fontsize=8)
        axis.set_yticks([period * 2 + 1 for period in range(self.parser.periods)])
        axis.set_yticklabels([str(period + 1) for period in range(self.parser.periods)], fontsize=8)
        axis.tick_params(length=0)
        axis.set_facecolor("#fcfcfc")

        capacity = self.parser.rooms.get(room_id, {}).get('capacity')
        if capacity is None:
            axis.set_title(room_id, fontsize=10)
        else:
            axis.set_title(f"{room_id} ({capacity})", fontsize=10)

        for day in range(self.parser.days + 1):
            axis.axvline(day, color="#d9d9d9", linewidth=0.8, zorder=0)
        for half_slot in range(self.half_periods_per_day + 1):
            linewidth = 0.8 if half_slot % 2 == 0 else 0.35
            color = "#d0d0d0" if half_slot % 2 == 0 else "#eeeeee"
            axis.axhline(half_slot, color=color, linewidth=linewidth, zorder=0)

    def _draw_gene(self, axis, gene, is_hard_violation):
        timeslot = gene['timeslot']
        day = timeslot // self.parser.periods
        period = timeslot % self.parser.periods
        y = period * 2
        height = gene.get('length', 2)
        facecolor = "#ff3b30" if is_hard_violation else self.course_colors.get(gene['course_id'], "#d7ecff")
        edgecolor = "#9b0000" if is_hard_violation else "#666666"
        text_color = "white" if is_hard_violation else "#202020"

        patch = self.Rectangle(
            (day + 0.06, y + 0.06),
            0.88,
            max(height - 0.12, 0.2),
            facecolor=facecolor,
            edgecolor=edgecolor,
            linewidth=2.0 if is_hard_violation else 1.0,
            zorder=2,
        )
        axis.add_patch(patch)
        axis.text(
            day + 0.5,
            y + (height / 2),
            self._format_gene_label(gene),
            ha="center",
            va="center",
            fontsize=7,
            color=text_color,
            zorder=3,
            clip_on=True,
        )

    def render(self, progress):
        if not self.is_open:
            return

        chromosome = progress.get('chromosome') or []
        hard_violations = set(progress.get('hc_violated_indices') or [])

        for room_id, axis in zip(self.room_ids, self.axes):
            self._configure_axis(axis, room_id)

        for gene_index, gene in enumerate(chromosome):
            room_id = gene.get('room_id')
            room_index = self.room_to_index.get(room_id)
            if room_index is None:
                continue
            axis = self.axes[room_index]
            self._draw_gene(axis, gene, gene_index in hard_violations)

        generation = progress.get('generation', 0)
        max_gen = progress.get('max_gen', 0)
        best_fitness = progress.get('best_fitness', 0)
        best_hc = progress.get('best_hc', 0)
        best_sc = progress.get('best_sc', 0)
        hc_weight = progress.get('hc_weight', 0)

        self.figure.suptitle(
            f"Generation {generation + 1}/{max_gen} | Fitness {best_fitness} | HC {best_hc} | SC {best_sc} | HC_W {hc_weight}",
            fontsize=13,
        )

        self.figure.canvas.draw()
        self.figure.canvas.flush_events()


def _render_worker(parser, data_queue):
    """
    子进程的入口函数：无限循环读取队列并画图。
    """
    renderer = _MatplotlibRenderer(parser)

    while renderer.is_open:
        try:
            # 阻塞等待数据，0.1秒超时用来检查窗口是否被关闭
            msg = data_queue.get(timeout=0.1)

            if msg == "STOP":
                break

            # 队列堆积处理：如果 GA 跑得太快塞了太多帧，我们只画最新的一帧，扔掉旧的
            while not data_queue.empty():
                try:
                    next_msg = data_queue.get_nowait()
                    if next_msg == "STOP":
                        msg = "STOP"
                        break
                    msg = next_msg
                except queue.Empty:
                    break

            if msg == "STOP":
                break

            # 渲染最新的一帧
            renderer.render(msg)

        except queue.Empty:
            # 队列为空时，触发一次 GUI 事件循环，防止窗口卡死
            renderer.figure.canvas.flush_events()
            continue
        except Exception as e:
            print(f"\n[可视化子进程] 绘图发生错误: {e}")
            break

    # 训练结束后保持窗口打开，直到用户手动关闭
    if renderer.is_open:
        print("\n[可视化提示] 训练完成！关闭可视化窗口即可退出。")
        renderer.plt.ioff()
        renderer.plt.show()


class TrainingProcessVisualizer:
    """
    代理类：运行在主进程中。
    它的任务只是把数据塞进多进程队列，瞬间返回，绝不阻塞 GA。
    """

    def __init__(self, parser, update_every=5, pause_seconds=0.001):
        # 初始化通信队列，最大容量设为 2（防止内存堆积）
        self.data_queue = mp.Queue(maxsize=2)

        # 启动子进程
        self.process = mp.Process(target=_render_worker, args=(parser, self.data_queue))
        # 设置为守护进程：主程序挂了，子进程也直接销毁，防止残留
        self.process.daemon = True
        self.process.start()

        # 逻辑判断状态
        self.last_rendered_hc = float('inf')
        self.last_rendered_sc = float('inf')
        self.last_render_time = 0.0
        # 即使是异步，也限制最高发送频率（0.1秒 = 10FPS 已经非常流畅了）
        self.min_render_interval = 0.1

    def __call__(self, progress):
        generation = progress.get('generation', 0)
        max_gen = progress.get('max_gen', 0)
        current_hc = progress.get('best_hc', 0)
        current_sc = progress.get('best_sc', 0)

        solved = (current_hc == 0 and current_sc == 0)
        is_last_generation = (max_gen > 0 and generation == max_gen - 1)
        current_time = time.time()

        should_send = False

        # 判断逻辑：是否应该发送这一帧给渲染进程？
        if generation == 0 or solved or is_last_generation:
            should_send = True
        elif (current_time - self.last_render_time) < self.min_render_interval:
            return
        elif current_hc < self.last_rendered_hc:
            should_send = True
        elif current_hc == 0 and (self.last_rendered_sc - current_sc) >= 10:
            should_send = True

        if should_send:
            self.last_rendered_hc = current_hc
            self.last_rendered_sc = current_sc
            self.last_render_time = current_time

            try:
                # 如果队列满了，强行挤掉旧的一帧（清空队列），把最新鲜的放进去
                if self.data_queue.full():
                    try:
                        self.data_queue.get_nowait()
                    except queue.Empty:
                        pass
                self.data_queue.put_nowait(progress)
            except Exception:
                pass  # 通信失败静默忽略，不影响主进程训练

    def close(self):
        """主进程结束时调用"""
        try:
            # 发送停止信号
            self.data_queue.put("STOP")
            # 等待子进程优雅结束（等待用户关闭最终的图表窗口）
            if self.process.is_alive():
                self.process.join()
        except Exception:
            pass