from collections import defaultdict


class BackendParserAdapter:
    def __init__(self, assembled_data):
        self.assembled_data = assembled_data
        self.metadata = {
            'Name': assembled_data['instance']['name'],
            'Days': assembled_data['instance']['days'],
            'Periods_per_day': assembled_data['instance']['periods_per_day'],
        }
        self.courses = assembled_data['courses']
        self.rooms = assembled_data['rooms']
        self.curricula = assembled_data['curricula']
        self.unavailability = assembled_data['unavailability']
        self.subcourses = assembled_data['subcourses']
        self._build_runtime_indexes()

        self.course_to_idx = {course_id: i for i, course_id in enumerate(self.courses.keys())}
        self.room_to_idx = {room_id: i for i, room_id in enumerate(self.rooms.keys())}
        self.curriculum_to_idx = {curriculum_id: i for i, curriculum_id in enumerate(self.curricula.keys())}
        self.subcourse_to_idx = {subcourse_id: i for i, subcourse_id in enumerate(self.subcourses.keys())}

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
        for curriculum_id, courses in self.curricula.items():
            for course_id in courses:
                course_to_curricula[course_id].append(curriculum_id)
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

        self.valid_rooms_by_course = {
            course_id: tuple(
                room_id for room_id in self.room_ids if self.room_capacity[room_id] >= self.course_students[course_id]
            )
            for course_id in self.courses
        }
