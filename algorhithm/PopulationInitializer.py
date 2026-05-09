import random
from collections import defaultdict


class PopulationInitializer:
    def __init__(self, parser, pop_size=30):
        """
        初始化种群生成器
        :param parser: 解析了数据的 CTTParser 实例
        :param pop_size: 种群大小 (论文 4.1节参数设置默认为 30)
        """
        self.parser = parser
        self.pop_size = pop_size

        self.days = parser.days
        self.periods = parser.periods
        self.num_timeslots = parser.num_timeslots
        self.num_half_slots = self.num_timeslots * 2

        self._build_auxiliary_maps()
        self.events_to_schedule = list(self.parser.subcourses.keys())
        self.room_ids = self.parser.room_ids
        self.valid_rooms_by_course = self.parser.valid_rooms_by_course

    def _build_auxiliary_maps(self):
        """构建用于快速检查约束的映射表"""
        self.course_to_curricula = self.parser.course_to_curricula
        self.course_unavailability = self.parser.course_unavailability_timeslots

    def _get_occupied_half_slots(self, timeslot, length):
        """
        获取某课程在给定起始 timeslot 下占用的所有半时隙索引
        :param timeslot: 起始 timeslot（大 period 索引）
        :param length: 课程长度（2倍整数，如3=1.5课时）
        :return: 占用的半时隙索引列表
        """
        base_half = timeslot * 2
        return [base_half + i for i in range(length)]

    def _validate_no_cross_day(self, timeslot, length):
        """
        验证课程长度不会跨越 day boundary
        :param timeslot: 起始 timeslot
        :param length: 课程长度（2倍整数）
        :return: True 表示合法，False 表示会跨天
        """
        start_half = timeslot * 2
        end_half = start_half + length
        half_per_day = self.periods * 2
        last_half_slot = end_half - 1
        start_day = start_half // half_per_day
        last_day = last_half_slot // half_per_day
        return start_day == last_day

    def _generate_greedy_individual(self):
        """
        生成单个个体（一条染色体），采用贪婪策略尽量满足硬约束
        """
        chromosome = []

        room_half_occupied = defaultdict(set)
        teacher_half_occupied = defaultdict(set)
        curriculum_half_occupied = defaultdict(set)

        shuffled_events = list(self.events_to_schedule)
        random.shuffle(shuffled_events)

        for subcourse_id in shuffled_events:
            subcourse_data = self.parser.subcourses[subcourse_id]
            course_id = subcourse_data['course_id']
            length = subcourse_data['length']
            week_signal = subcourse_data['week_signal']
            lecture_time = subcourse_data['lecture_time']

            teachers = self.parser.course_teachers[course_id]
            curricula = self.course_to_curricula[course_id]
            unavail_slots = self.course_unavailability[course_id]
            candidate_rooms = self.valid_rooms_by_course[course_id]

            candidate_timeslots = list(range(self.num_timeslots))
            random.shuffle(candidate_timeslots)
            chosen_t = None
            chosen_r = None

            for t in candidate_timeslots:
                if t in unavail_slots:
                    continue
                if not self._validate_no_cross_day(t, length):
                    continue

                half_slots_t = self._get_occupied_half_slots(t, length)
                if any(hs in teacher_half_occupied[teacher] for teacher in teachers for hs in half_slots_t):
                    continue
                if any(hs in curriculum_half_occupied[curriculum] for curriculum in curricula for hs in half_slots_t):
                    continue

                shuffled_rooms = list(candidate_rooms)
                random.shuffle(shuffled_rooms)
                for room_id in shuffled_rooms:
                    if any((room_id, hs) in room_half_occupied for hs in half_slots_t):
                        continue
                    chosen_t = t
                    chosen_r = room_id
                    break

                if chosen_t is not None:
                    break

            if chosen_t is None:
                chosen_t = random.randint(0, self.num_timeslots - 1)
                if not self._validate_no_cross_day(chosen_t, length):
                    for t in range(self.num_timeslots):
                        if self._validate_no_cross_day(t, length):
                            chosen_t = t
                            break
                fallback_rooms = candidate_rooms or self.room_ids
                chosen_r = random.choice(fallback_rooms)

            gene = {
                'course_id': course_id,
                'subcourse_id': subcourse_id,
                'timeslot': chosen_t,
                'room_id': chosen_r,
                'length': length,
                'week_signal': week_signal,
                'lecture_time': lecture_time
            }
            chromosome.append(gene)

            half_slots = self._get_occupied_half_slots(chosen_t, length)
            for hs in half_slots:
                room_half_occupied[(chosen_r, hs)].add((len(chromosome) - 1, week_signal))
            for teacher in teachers:
                teacher_half_occupied[teacher].update(half_slots)
            for curriculum in curricula:
                curriculum_half_occupied[curriculum].update(half_slots)

        return chromosome

    def initialize_population(self):
        """生成整个初始种群"""
        population = []
        print(f"正在使用贪婪策略生成初始种群 (大小: {self.pop_size})...")
        for _ in range(self.pop_size):
            ind = self._generate_greedy_individual()
            population.append(ind)
        print("初始种群生成完毕！")
        return population


# --- 测试与展示代码 ---
if __name__ == "__main__":
    from CTTParser import CTTParser  # 导入你的 Parser

    # 1. 读取数据
    parser = CTTParser('comp21.ctt')

    # 2. 实例化初始化器
    initializer = PopulationInitializer(parser, pop_size=30)

    # 3. 生成种群
    population = initializer.initialize_population()

    # 4. 打印验证
    print(f"\n染色体长度 (总事件数): {len(population[0])}")
    print(f"子课程数: {len(parser.subcourses)}")
    print("\n展示种群中第一个个体的部分基因 (前 5 个事件):")
    for idx, gene in enumerate(population[0][:5]):
        day = gene['timeslot'] // initializer.periods
        period = gene['timeslot'] % initializer.periods
        print(f"Gene {idx + 1}: 课程 {gene['course_id']:<6} 子课 {gene['subcourse_id']:<12} "
              f"-> Room {gene['room_id']:<5} | 时间: Day {day}, Period {period} "
              f"| length={gene['length']}, week={gene['week_signal']}")

    target_course = None
    target_lectures = 0
    for c_id, c_data in parser.courses.items():
        if c_data['nr_lectures'] > 1:
            target_course = c_id
            target_lectures = c_data['nr_lectures']
            break

    if target_course:
        print(f"\n{'=' * 50}")
        print(f"专项验证: 课程 {target_course} 一周需上课 {target_lectures} 次")
        print(f"{'=' * 50}")

        course_genes = [gene for gene in population[0] if gene['course_id'] == target_course]
        course_genes.sort(key=lambda x: x['timeslot'])

        for i, gene in enumerate(course_genes):
            day = gene['timeslot'] // initializer.periods
            period = gene['timeslot'] % initializer.periods

            print(f"子课程 {i + 1}/{target_lectures}: {gene['subcourse_id']} "
                  f"被分配在教室 {gene['room_id']:<5} | "
                  f"时间: 第 {day} 天, 第 {period} 节 (全局时段索引 {gene['timeslot']})")
        print(f"{'=' * 50}")
