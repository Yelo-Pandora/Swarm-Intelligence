import json
from collections import defaultdict


class CTTParser:
    def __init__(self, file_path):
        self.file_path = file_path
        # 初始化属性
        self.metadata = {}
        self.courses = {}
        self.rooms = {}
        self.curricula = {}
        self.unavailability = []
        self.subcourses = {}  # 子课程字典 {subcourse_id: {course_id, week_signal, lecture_time, length}}

        # 自动执行解析
        self._parse()
        self._build_runtime_indexes()

        # 衍生属性：生成 RL 所需的 ID 到 索引的映射
        self.course_to_idx = {c_id: i for i, c_id in enumerate(self.courses.keys())}
        self.room_to_idx = {r_id: i for i, r_id in enumerate(self.rooms.keys())}
        self.curriculum_to_idx = {l_id: i for i, l_id in enumerate(self.curricula.keys())}
        self.subcourse_to_idx = {sc_id: i for i, sc_id in enumerate(self.subcourses.keys())}

    def _parse(self):
        current_section = None
        with open(self.file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line == 'END.':
                    continue

                # 解析元数据
                if ':' in line and not line.endswith(':'):
                    key, value = line.split(':', 1)
                    self.metadata[key.strip()] = value.strip()
                    continue

                # 识别数据段
                if line.endswith(':'):
                    current_section = line[:-1].strip()
                    continue

                parts = line.split()

                # 根据不同段填充属性
                if current_section == 'COURSES':
                    teacher_list = parts[1].split(',')
                    # 向后兼容：支持扩展字段 length 和 week_signal
                    # 格式: course_id teachers nr_lectures min_days nr_students [length [week_signal]]
                    course_length = int(parts[5]) if len(parts) > 5 else 2  # 变长课程：2倍整数存储（如3=1.5课时），默认1课时
                    course_week_signal = int(parts[6]) if len(parts) > 6 else 0  # 单双周：0=每周, 1=单周, 2=双周，默认每周

                    self.courses[parts[0]] = {
                        'teachers': teacher_list,
                        'nr_lectures': int(parts[2]),
                        'min_days': int(parts[3]),
                        'nr_students': int(parts[4]),
                        'length': course_length
                        # week_signal 附加在 subcourse 上，不放在 course 中
                    }
                    # 生成 subcourses 列表（每个 lecture 一个 subcourse）
                    for lecture_idx in range(1, int(parts[2]) + 1):
                        subcourse_id = f"{parts[0]}_lec{lecture_idx}"
                        self.subcourses[subcourse_id] = {
                            'course_id': parts[0],
                            'week_signal': course_week_signal,  # week_signal 附加在 subcourse 上
                            'lecture_time': lecture_idx,
                            'length': course_length
                        }
                elif current_section == 'ROOMS':
                    self.rooms[parts[0]] = {
                        'capacity': int(parts[1])
                    }
                elif current_section == 'CURRICULA':
                    # 格式: ID Count Course1 Course2...
                    self.curricula[parts[0]] = parts[2:]
                elif current_section == 'UNAVAILABILITY_CONSTRAINTS':
                    self.unavailability.append({
                        'course_id': parts[0],
                        'day': int(parts[1]),
                        'slot': int(parts[2])
                    })

    def _build_runtime_indexes(self):
        self.days = int(self.metadata.get('Days', 5))
        self.periods = int(self.metadata.get('Periods_per_day', 6))
        self.num_timeslots = self.days * self.periods
        self.room_ids = tuple(self.rooms.keys())
        self.room_capacity = {room_id: data['capacity'] for room_id, data in self.rooms.items()}
        self.course_teachers = {course_id: tuple(data['teachers']) for course_id, data in self.courses.items()}
        self.course_students = {course_id: data['nr_students'] for course_id, data in self.courses.items()}
        self.course_min_days = {course_id: data['min_days'] for course_id, data in self.courses.items()}

        course_to_curricula = defaultdict(list)
        for curr_id, courses in self.curricula.items():
            for course_id in courses:
                course_to_curricula[course_id].append(curr_id)
        self.course_to_curricula = {course_id: tuple(curricula) for course_id, curricula in course_to_curricula.items()}
        for course_id in self.courses:
            self.course_to_curricula.setdefault(course_id, tuple())

        course_unavailability_timeslots = defaultdict(set)
        for unavail in self.unavailability:
            timeslot = unavail['day'] * self.periods + unavail['slot']
            course_unavailability_timeslots[unavail['course_id']].add(timeslot)
        self.course_unavailability_timeslots = {
            course_id: frozenset(timeslots)
            for course_id, timeslots in course_unavailability_timeslots.items()
        }
        for course_id in self.courses:
            self.course_unavailability_timeslots.setdefault(course_id, frozenset())

        valid_rooms_by_course = {}
        for course_id, students in self.course_students.items():
            valid_rooms_by_course[course_id] = tuple(
                room_id for room_id in self.room_ids if self.room_capacity[room_id] >= students
            )
        self.valid_rooms_by_course = valid_rooms_by_course

    def display(self):
        """完整打印数据结构（结构化美化版）"""
        print("=" * 20 + " CTT DATA STRUCTURE " + "=" * 20)

        # 构建一个临时的总字典用于展示
        full_structure = {
            "Metadata": self.metadata,
            "Courses (Count: {})".format(len(self.courses)): self.courses,
            "Rooms (Count: {})".format(len(self.rooms)): self.rooms,
            "Curricula (Count: {})".format(len(self.curricula)): self.curricula,
            "Unavailability Constraints (Count: {})".format(len(self.unavailability)): self.unavailability[:5]  # 仅展示前5条
        }

        # 使用 json.dumps 进行缩进打印
        print(json.dumps(full_structure, indent=4, ensure_ascii=False))
        print("\n" + "=" * 60)

    def get_summary(self):
        """打印简单的统计摘要"""
        print(f"实例名称: {self.metadata.get('Name')}")
        print(f"天数: {self.metadata.get('Days')}, 每天时段: {self.metadata.get('Periods_per_day')}")
        print(f"课程数: {len(self.courses)}, 教室数: {len(self.rooms)}, 课程组数: {len(self.curricula)}, 子课程数: {len(self.subcourses)}")

    def display_courses_with_subcourses(self):
        """打印课程及其子课程的详细信息"""
        print("\n" + "=" * 80)
        print("课程详情 (含子课程信息)")
        print("=" * 80)

        for course_id, c_data in self.courses.items():
            # 课程基本信息
            subcourses_of_course = {k: v for k, v in self.subcourses.items() if v['course_id'] == course_id}

            print(f"\n【{course_id}】")
            print(f"  教师: {c_data['teachers']}")
            print(f"  每周授课次数: {c_data['nr_lectures']}")
            print(f"  最少分布天数: {c_data['min_days']}")
            print(f"  学生人数: {c_data['nr_students']}")
            print(f"  课程长度: {c_data['length']}/2 = {c_data['length']/2} 课时")
            print(f"  子课程数: {len(subcourses_of_course)}")

            # 子课程详情
            for sc_id, sc_data in subcourses_of_course.items():
                week_label = {0: "每周", 1: "单周", 2: "双周"}.get(sc_data['week_signal'], "未知")
                print(f"    └─ {sc_id}: 第{sc_data['lecture_time']}次课 | {week_label} | 长度={sc_data['length']}/2={sc_data['length']/2}课时")

    def display_rooms(self):
        """打印教室信息"""
        print("\n" + "=" * 40)
        print("教室信息")
        print("=" * 40)
        for room_id, r_data in self.rooms.items():
            print(f"  {room_id}: 容量 {r_data['capacity']} 人")


# --- 使用方法 ---
if __name__ == "__main__":
    import sys
    import os
    # 支持命令行参数指定文件
    if len(sys.argv) > 1:
        ctt_file = sys.argv[1]
    else:
        # 默认使用 algorithm 目录下的文件
        ctt_file = os.path.join(os.path.dirname(__file__), 'comp21.ctt')

    print(f"正在解析文件: {ctt_file}\n")
    data_loader = CTTParser(ctt_file)

    # 1. 打印统计信息
    print("=" * 60)
    print("统计摘要")
    print("=" * 60)
    data_loader.get_summary()

    # 2. 打印课程详情（含子课程）
    data_loader.display_courses_with_subcourses()

    # 3. 打印教室信息
    data_loader.display_rooms()

    # 4. 完整查看原始数据结构
    print("\n" + "=" * 60)
    print("原始数据结构 (JSON)")
    print("=" * 60)
    data_loader.display()

    # 5. 演示如何直接访问属性
    print("\n" + "=" * 60)
    print("访问示例")
    print("=" * 60)
    example_course = list(data_loader.courses.keys())[0]
    print(f"课程ID: {example_course}")
    print(f"课程信息: {data_loader.courses[example_course]}")
    print(f"该课程在 RL 矩阵中的索引: {data_loader.course_to_idx[example_course]}")

    # 展示该课程的子课程
    example_subcourses = {k: v for k, v in data_loader.subcourses.items()
                         if v['course_id'] == example_course}
    print(f"该课程的子课程: {example_subcourses}")