import csv
import os
import sys
import random
import copy
import math
from collections import defaultdict


def week_signals_conflict(ws1, ws2):
    """
    判断两个 week_signal 是否冲突
    - 0=每周，与谁都冲突
    - 1=单周，与1(单周)、0(每周)冲突，不与2(双周)冲突
    - 2=双周，与2(双周)、0(每周)冲突，不与1(单周)冲突
    """
    if ws1 == 0 or ws2 == 0:
        return True
    return ws1 == ws2


class FitnessEvaluator:
    def __init__(self, parser, hc_weight=1000, sc_weight=5):
        self.parser = parser
        self.hc_weight = hc_weight
        self.sc_weight = sc_weight

        self.days = parser.days
        self.periods = parser.periods
        self.num_timeslots = parser.num_timeslots
        self.num_half_slots = self.num_timeslots * 2

        self.room_capacity = parser.room_capacity
        self.course_students = parser.course_students
        self.course_min_days = parser.course_min_days
        self.course_teachers = parser.course_teachers
        self.course_to_curricula = parser.course_to_curricula
        self.course_unavailability = parser.course_unavailability_timeslots
        self.default_period_mode = all(
            subcourse.get('length', 2) == 2 and subcourse.get('week_signal', 0) == 0
            for subcourse in parser.subcourses.values()
        )

    @staticmethod
    def get_occupied_half_slots(gene, periods):
        """
        返回某 gene 占用的所有半时隙索引列表
        :param gene: 基因字典
        :param periods: 每天的 period 数
        :return: 半时隙索引列表
        """
        base_timeslot = gene['timeslot']
        length = gene.get('length', 2)
        return [base_timeslot * 2 + i for i in range(length)]

    @staticmethod
    def validate_no_cross_day(gene, periods):
        """
        验证课程长度不会跨越 day boundary
        """
        length = gene.get('length', 2)
        start_half = gene['timeslot'] * 2
        end_half = start_half + length
        half_per_day = periods * 2
        last_half_slot = end_half - 1
        start_day = start_half // half_per_day
        last_day = last_half_slot // half_per_day
        return start_day == last_day

    @staticmethod
    def is_default_period_gene(gene):
        return gene.get('length', 2) == 2 and gene.get('week_signal', 0) == 0

    def _evaluate_soft_constraints(self, chromosome, course_days, course_rooms, curriculum_ts_set,
                                   course_to_gene_indices, curr_t_to_idx, sc_violated_indices):
        sc_violations = 0

        for c_id, days in course_days.items():
            min_days = self.course_min_days[c_id]
            if len(days) < min_days:
                sc_violations += (min_days - len(days))
                sc_violated_indices.update(course_to_gene_indices[c_id])

        for c_id, rooms in course_rooms.items():
            if len(rooms) > 1:
                sc_violations += (len(rooms) - 1)
                sc_violated_indices.update(course_to_gene_indices[c_id])

        for curr_id, timeslots in curriculum_ts_set.items():
            for t in timeslots:
                period = t % self.periods
                is_isolated = True
                if period > 0 and (t - 1) in timeslots:
                    is_isolated = False
                if period < self.periods - 1 and (t + 1) in timeslots:
                    is_isolated = False

                if is_isolated:
                    sc_violations += 1
                    if (curr_id, t) in curr_t_to_idx:
                        sc_violated_indices.add(curr_t_to_idx[(curr_id, t)])

        return sc_violations

    def _evaluate_period_based(self, chromosome):
        hc_violations = 0
        hc_violated_indices = set()
        sc_violated_indices = set()

        teacher_ts = defaultdict(list)
        curriculum_ts = defaultdict(list)
        room_ts = defaultdict(list)

        course_days = defaultdict(set)
        course_rooms = defaultdict(set)
        curriculum_ts_set = defaultdict(set)
        course_to_gene_indices = defaultdict(list)
        curr_t_to_idx = {}

        for idx, gene in enumerate(chromosome):
            c_id = gene['course_id']
            t_id = gene['timeslot']
            r_id = gene['room_id']
            course_data = self.parser.courses[c_id]
            day = t_id // self.periods

            course_days[c_id].add(day)
            course_rooms[c_id].add(r_id)
            course_to_gene_indices[c_id].append(idx)

            room_ts[(r_id, t_id)].append(idx)
            if len(room_ts[(r_id, t_id)]) > 1:
                hc_violations += 1
                hc_violated_indices.update(room_ts[(r_id, t_id)])

            if self.course_students[c_id] > self.room_capacity[r_id]:
                hc_violations += 1
                hc_violated_indices.add(idx)

            if t_id in self.course_unavailability[c_id]:
                hc_violations += 1
                hc_violated_indices.add(idx)

            for teacher in self.course_teachers[c_id]:
                teacher_ts[teacher].append((t_id, idx))

            for curr_id in self.course_to_curricula[c_id]:
                curriculum_ts[curr_id].append((t_id, idx))
                curriculum_ts_set[curr_id].add(t_id)
                curr_t_to_idx[(curr_id, t_id)] = idx

        for ts_dict in [teacher_ts, curriculum_ts]:
            for _, events in ts_dict.items():
                ts_counts = defaultdict(list)
                for t, idx in events:
                    ts_counts[t].append(idx)
                for _, indices in ts_counts.items():
                    if len(indices) > 1:
                        hc_violations += len(indices) - 1
                        hc_violated_indices.update(indices)

        sc_violations = self._evaluate_soft_constraints(
            chromosome,
            course_days,
            course_rooms,
            curriculum_ts_set,
            course_to_gene_indices,
            curr_t_to_idx,
            sc_violated_indices,
        )

        fitness = (hc_violations * self.hc_weight) + (sc_violations * self.sc_weight)
        return fitness, hc_violations, sc_violations, list(hc_violated_indices), list(sc_violated_indices)

    def _evaluate_half_slot_based(self, chromosome):
        hc_violations = 0
        sc_violations = 0
        hc_violated_indices = set()
        sc_violated_indices = set()

        room_half_ts = defaultdict(list)
        teacher_half_ts = defaultdict(list)
        curriculum_half_ts = defaultdict(list)

        course_days = defaultdict(set)
        course_rooms = defaultdict(set)
        curriculum_ts_set = defaultdict(set)

        course_to_gene_indices = defaultdict(list)
        curr_t_to_idx = {}

        for idx, gene in enumerate(chromosome):
            c_id = gene['course_id']
            t_id = gene['timeslot']
            r_id = gene['room_id']
            ws = gene.get('week_signal', 0)

            course_data = self.parser.courses[c_id]
            day = t_id // self.periods

            course_days[c_id].add(day)
            course_rooms[c_id].add(r_id)
            course_to_gene_indices[c_id].append(idx)

            half_slots = self.get_occupied_half_slots(gene, self.periods)

            for hs in half_slots:
                room_half_ts[(r_id, hs)].append((idx, ws))

            if self.course_students[c_id] > self.room_capacity[r_id]:
                hc_violations += 1
                hc_violated_indices.add(idx)

            if t_id in self.course_unavailability[c_id]:
                hc_violations += 1
                hc_violated_indices.add(idx)

            for teacher in self.course_teachers[c_id]:
                for hs in half_slots:
                    teacher_half_ts[teacher].append((hs, idx))

            for curr_id in self.course_to_curricula[c_id]:
                for hs in half_slots:
                    curriculum_half_ts[curr_id].append((hs, idx))
                curriculum_ts_set[curr_id].add(t_id)
                curr_t_to_idx[(curr_id, t_id)] = idx

        for _, entries in room_half_ts.items():
            if len(entries) <= 1:
                continue
            conflict_count = 0
            for i in range(len(entries)):
                for j in range(i + 1, len(entries)):
                    idx_i, ws_i = entries[i]
                    idx_j, ws_j = entries[j]
                    if week_signals_conflict(ws_i, ws_j):
                        conflict_count += 1
                        hc_violated_indices.update([idx_i, idx_j])
            if conflict_count > 0:
                hc_violations += 1

        for _, events in teacher_half_ts.items():
            hs_to_genes = defaultdict(list)
            for hs, idx in events:
                hs_to_genes[hs].append(idx)

            for _, indices in hs_to_genes.items():
                if len(indices) <= 1:
                    continue
                for i in range(len(indices)):
                    for j in range(i + 1, len(indices)):
                        g1 = chromosome[indices[i]]
                        g2 = chromosome[indices[j]]
                        ws1 = g1.get('week_signal', 0)
                        ws2 = g2.get('week_signal', 0)
                        if week_signals_conflict(ws1, ws2):
                            hc_violations += 1
                            hc_violated_indices.update([indices[i], indices[j]])

        for _, events in curriculum_half_ts.items():
            hs_to_genes = defaultdict(list)
            for hs, idx in events:
                hs_to_genes[hs].append(idx)

            for _, indices in hs_to_genes.items():
                if len(indices) <= 1:
                    continue
                for i in range(len(indices)):
                    for j in range(i + 1, len(indices)):
                        g1 = chromosome[indices[i]]
                        g2 = chromosome[indices[j]]
                        ws1 = g1.get('week_signal', 0)
                        ws2 = g2.get('week_signal', 0)
                        if week_signals_conflict(ws1, ws2):
                            hc_violations += 1
                            hc_violated_indices.update([indices[i], indices[j]])

        sc_violations = self._evaluate_soft_constraints(
            chromosome,
            course_days,
            course_rooms,
            curriculum_ts_set,
            course_to_gene_indices,
            curr_t_to_idx,
            sc_violated_indices,
        )

        fitness = (hc_violations * self.hc_weight) + (sc_violations * self.sc_weight)
        return fitness, hc_violations, sc_violations, list(hc_violated_indices), list(sc_violated_indices)

    def evaluate(self, chromosome):
        """返回两套独立的黑名单：(总分, HC数, SC数, HC基因列表, SC基因列表)"""
        if self.default_period_mode:
            return self._evaluate_period_based(chromosome)
        return self._evaluate_half_slot_based(chromosome)


class MDGAOptimizer:
    # __init__, _roulette_wheel_selection, _crossover, _Swap_genes, M1, M2, M3 的函数体保持完全一致，无需改动
    def __init__(self, parser, initial_population, max_gen=3000, progress_callback=None, stop_checker=None):
        self.parser = parser
        self.pop = initial_population
        self.pop_size = len(initial_population)
        self.evaluator = FitnessEvaluator(parser)

        self.p_m = 0.15
        self.p_c = 0.8
        self.max_gen = max_gen
        self.progress_callback = progress_callback
        self.stop_checker = stop_checker
        self.num_timeslots = parser.num_timeslots
        self.periods = parser.periods
        self.all_rooms = list(parser.room_ids)
        self.all_room_set = set(self.all_rooms)
        self.room_capacity = parser.room_capacity
        self.course_teachers = parser.course_teachers
        self.course_to_curricula = parser.course_to_curricula
        self.valid_rooms_by_course = parser.valid_rooms_by_course

        self.idx_to_course = {i: gene['course_id'] for i, gene in enumerate(self.pop[0])}
        self.idx_to_capacity = {i: parser.course_students[self.idx_to_course[i]] for i in self.idx_to_course}
        self.idx_to_length = {i: gene.get('length', 2) for i, gene in enumerate(self.pop[0])}
        self.idx_to_teachers = {i: self.course_teachers[self.idx_to_course[i]] for i in self.idx_to_course}
        self.idx_to_curricula = {i: self.course_to_curricula[self.idx_to_course[i]] for i in self.idx_to_course}
        self.idx_to_valid_rooms = {
            i: (self.valid_rooms_by_course[self.idx_to_course[i]] or parser.room_ids)
            for i in self.idx_to_course
        }
        self.capacity_to_indices = defaultdict(list)

        for idx in self.idx_to_course:
            cap_group = self.idx_to_capacity[idx] // 10
            self.capacity_to_indices[cap_group].append(idx)

    def _is_valid_slot_for_length(self, timeslot, length):
        """
        检查给定的起始 slot 是否能容纳指定长度的课程（不跨天）
        """
        start_half = timeslot * 2
        end_half = start_half + length
        last_half_slot = end_half - 1
        start_day = start_half // (self.periods * 2)
        last_day = last_half_slot // (self.periods * 2)
        return start_day == last_day

    def _get_gene_half_slots(self, gene, timeslot=None):
        candidate_gene = gene if timeslot is None else {**gene, 'timeslot': timeslot}
        return FitnessEvaluator.get_occupied_half_slots(candidate_gene, self.periods)

    def _room_conflict_indices(self, ind, idx, timeslot, room_id):
        gene = ind[idx]
        candidate_half_slots = self._get_gene_half_slots(gene, timeslot)
        candidate_ws = gene.get('week_signal', 0)
        conflicts = []

        for other_idx, other_gene in enumerate(ind):
            if other_idx == idx or other_gene['room_id'] != room_id:
                continue
            if not week_signals_conflict(candidate_ws, other_gene.get('week_signal', 0)):
                continue

            other_half_slots = self._get_gene_half_slots(other_gene)
            if any(hs in other_half_slots for hs in candidate_half_slots):
                conflicts.append(other_idx)

        return conflicts

    def _placement_has_resource_conflict(self, ind, idx, timeslot, room_id):
        gene = ind[idx]
        candidate_half_slots = self._get_gene_half_slots(gene, timeslot)
        candidate_teachers = self.idx_to_teachers[idx]
        candidate_curricula = self.idx_to_curricula[idx]
        candidate_ws = gene.get('week_signal', 0)

        if self._room_conflict_indices(ind, idx, timeslot, room_id):
            return True

        candidate_half_slot_set = set(candidate_half_slots)
        for other_idx, other_gene in enumerate(ind):
            if other_idx == idx:
                continue
            if not week_signals_conflict(candidate_ws, other_gene.get('week_signal', 0)):
                continue

            other_half_slots = self._get_gene_half_slots(other_gene)
            if not candidate_half_slot_set.intersection(other_half_slots):
                continue

            if set(candidate_teachers) & set(self.idx_to_teachers[other_idx]):
                return True

            if set(candidate_curricula) & set(self.idx_to_curricula[other_idx]):
                return True

        return False

    def _is_valid_placement(self, ind, idx, timeslot, room_id):
        if not self._is_valid_slot_for_length(timeslot, self.idx_to_length[idx]):
            return False
        if self.room_capacity[room_id] < self.idx_to_capacity[idx]:
            return False
        return not self._placement_has_resource_conflict(ind, idx, timeslot, room_id)

    def _roulette_wheel_selection(self, population_with_fitness):
        max_fit = max(f[1] for f in population_with_fitness)
        inverse_fitnesses = [max_fit - f[1] + 1e-4 for f in population_with_fitness]
        total_inv_fit = sum(inverse_fitnesses)
        probs = [inv / total_inv_fit for inv in inverse_fitnesses]
        indices = random.choices(range(self.pop_size), weights=probs, k=2)
        return copy.deepcopy(population_with_fitness[indices[0]]), copy.deepcopy(population_with_fitness[indices[1]])

    def _crossover(self, p1, p2, v_idx1, v_idx2):
        if random.random() < self.p_c:
            target_indices = list(set(v_idx1) | set(v_idx2))
            if not target_indices:
                target_indices = random.sample(range(len(p1)), int(len(p1) * 0.1))
            else:
                target_indices = random.sample(target_indices, min(len(target_indices), int(len(p1) * 0.15)))
            for idx in target_indices:
                p1[idx]['timeslot'], p2[idx]['timeslot'] = p2[idx]['timeslot'], p1[idx]['timeslot']
                p1[idx]['room_id'], p2[idx]['room_id'] = p2[idx]['room_id'], p1[idx]['room_id']
        return p1, p2

    def _swap_genes(self, ind, idx1, idx2):
        ind[idx1]['timeslot'], ind[idx2]['timeslot'] = ind[idx2]['timeslot'], ind[idx1]['timeslot']
        ind[idx1]['room_id'], ind[idx2]['room_id'] = ind[idx2]['room_id'], ind[idx1]['room_id']

    def _mutation_M1(self, ind, violated_indices):
        if random.random() < self.p_m and violated_indices:
            mut_indices = random.sample(violated_indices, max(1, int(len(violated_indices) * 0.3)))

            for idx in mut_indices:
                candidate_timeslots = list(range(self.num_timeslots))
                random.shuffle(candidate_timeslots)
                chosen_packet = None

                for new_t in candidate_timeslots:
                    if not self._is_valid_slot_for_length(new_t, self.idx_to_length[idx]):
                        continue
                    candidate_rooms = list(self.idx_to_valid_rooms[idx])
                    random.shuffle(candidate_rooms)
                    for new_r in candidate_rooms:
                        if self._is_valid_placement(ind, idx, new_t, new_r):
                            chosen_packet = (new_t, new_r)
                            break
                    if chosen_packet is not None:
                        break

                if chosen_packet is not None:
                    ind[idx]['timeslot'], ind[idx]['room_id'] = chosen_packet
                else:
                    suitable_occupied = [
                        i for i in range(len(ind))
                        if i != idx and self.room_capacity[ind[i]['room_id']] >= self.idx_to_capacity[idx]
                    ]
                    if suitable_occupied:
                        self._swap_genes(ind, idx, random.choice(suitable_occupied))
        return ind

    def _mutation_M2(self, ind, violated_indices, current_eval, gen):
        current_fit = current_eval[0]
        if random.random() < self.p_m and violated_indices:
            idx1 = random.choice(violated_indices)
            cands = [i for i in range(len(ind)) if i != idx1]
            if cands:
                idx2 = random.choice(cands)
                self._swap_genes(ind, idx1, idx2)

                new_eval = self.evaluator.evaluate(ind)
                delta = new_eval[0] - current_fit
                if delta < 0:
                    return ind, new_eval
                temperature = max(0.1, 500 * (1 - gen / self.max_gen))
                if random.random() < math.exp(-delta / temperature):
                    return ind, new_eval
                self._swap_genes(ind, idx1, idx2)
        return ind, current_eval

    def _mutation_M3(self, ind, violated_indices, current_eval, gen):
        current_fit = current_eval[0]
        if random.random() < self.p_m and violated_indices:
            idx1 = random.choice(violated_indices)
            cap_group = self.idx_to_capacity[idx1] // 10
            cands = [i for i in self.capacity_to_indices[cap_group] if i != idx1]
            if len(cands) >= 2:
                target_a, target_b = random.sample(cands, 2)

                self._swap_genes(ind, idx1, target_a)
                eval_a = self.evaluator.evaluate(ind)
                self._swap_genes(ind, idx1, target_a)

                self._swap_genes(ind, idx1, target_b)
                eval_b = self.evaluator.evaluate(ind)
                self._swap_genes(ind, idx1, target_b)

                best_target, best_eval = (target_a, eval_a) if eval_a[0] < eval_b[0] else (target_b, eval_b)
                delta = best_eval[0] - current_fit
                if delta < 0:
                    self._swap_genes(ind, idx1, best_target)
                    return ind, best_eval
                temperature = max(0.1, 500 * (1 - gen / self.max_gen))
                if random.random() < math.exp(-delta / temperature):
                    self._swap_genes(ind, idx1, best_target)
                    return ind, best_eval
        return ind, current_eval

    def _ejection_chain_repair(self, ind, hc_indices):
        if not hc_indices:
            return ind
        best_ind = copy.deepcopy(ind)
        _, best_hc, _, _, _ = self.evaluator.evaluate(best_ind)

        for _ in range(20):
            temp_ind = copy.deepcopy(best_ind)
            src_idx = random.choice(hc_indices)

            for _ in range(5):
                valid_rooms = self.idx_to_valid_rooms[src_idx]
                if not valid_rooms:
                    break

                valid_target = False
                occupants = []
                for _ in range(100):
                    target_t = random.randint(0, self.num_timeslots - 1)
                    target_r = random.choice(valid_rooms)
                    if not self._is_valid_slot_for_length(target_t, self.idx_to_length[src_idx]):
                        continue

                    occupants = self._room_conflict_indices(temp_ind, src_idx, target_t, target_r)
                    temp_ind[src_idx]['timeslot'] = target_t
                    temp_ind[src_idx]['room_id'] = target_r
                    valid_target = True
                    break

                if not valid_target:
                    break

                if not occupants:
                    break

                src_idx = occupants[0]

            _, new_hc, _, _, _ = self.evaluator.evaluate(temp_ind)
            if new_hc < best_hc:
                best_hc = new_hc
                best_ind = temp_ind
                if best_hc == 0:
                    break

        return best_ind

    def run(self):
        print(f"\n--- 开始终极版 MDGA 优化 (目标精准锁定版) ---")
        current_pop = self.pop
        best_overall = None
        best_fitness = float('inf')

        last_best_hc = float('inf')
        stagnation_counter = 0

        for gen in range(self.max_gen):
            if self.stop_checker and self.stop_checker():
                break

            pop_eval = []
            for ind in current_pop:
                fit, hc_v, sc_v, hc_idx, sc_idx = self.evaluator.evaluate(ind)
                pop_eval.append((ind, fit, hc_v, sc_v, hc_idx, sc_idx))
                if fit < best_fitness:
                    best_fitness = fit
                    best_overall = (copy.deepcopy(ind), fit, hc_v, sc_v)

            pop_eval.sort(key=lambda x: x[1])
            best_curr = pop_eval[0]

            if self.progress_callback:
                self.progress_callback({
                    'generation': gen,
                    'max_gen': self.max_gen,
                    'best_fitness': best_curr[1],
                    'best_hc': best_curr[2],
                    'best_sc': best_curr[3],
                    'hc_weight': self.evaluator.hc_weight,
                    'chromosome': best_curr[0],
                    'hc_violated_indices': best_curr[4],
                    'sc_violated_indices': best_curr[5],
                })

            if gen < 500 and best_curr[2] > 0:
                self.evaluator.sc_weight = 0
            else:
                self.evaluator.sc_weight = 5

            if best_curr[2] < last_best_hc:
                last_best_hc = best_curr[2]
                stagnation_counter = 0
                if self.evaluator.hc_weight > 1000:
                    self.evaluator.hc_weight = max(1000, self.evaluator.hc_weight // 2)
            else:
                stagnation_counter += 1

            if stagnation_counter >= 30 and best_curr[2] > 0:
                self.evaluator.hc_weight = min(100000, self.evaluator.hc_weight * 2)
                stagnation_counter = 0
                print(f"  --> [系统警报] 陷入死锁 (HC={best_curr[2]})，执行修复...")

                repaired_elite = self._ejection_chain_repair(copy.deepcopy(best_curr[0]), best_curr[4])
                current_pop[0] = repaired_elite

                half = self.pop_size // 2
                for i in range(half, self.pop_size):
                    shuffle_size = int(len(current_pop[i]) * 0.5)
                    idx_to_shuffle = random.sample(range(len(current_pop[i])), shuffle_size)
                    for idx in idx_to_shuffle:
                        length = self.idx_to_length[idx]
                        new_slot = random.randint(0, self.num_timeslots - 1)
                        while not self._is_valid_slot_for_length(new_slot, length):
                            new_slot = random.randint(0, self.num_timeslots - 1)
                        current_pop[i][idx]['timeslot'] = new_slot
                        current_pop[i][idx]['room_id'] = random.choice(self.all_rooms)

                continue

            if gen % 10 == 0 or gen == self.max_gen - 1:
                print(
                    f"Gen {gen:4d} | Fit: {best_curr[1]:7d} | HC: {best_curr[2]:3d} | SC: {best_curr[3]:3d} | HC_W: {self.evaluator.hc_weight}")

            if best_curr[2] == 0 and best_curr[3] == 0:
                print(f"\n训练已提前终止。")
                break

            new_pop = []
            new_pop.append(copy.deepcopy(pop_eval[0][0]))
            new_pop.append(copy.deepcopy(pop_eval[1][0]))

            while len(new_pop) < self.pop_size:
                p1_data, p2_data = self._roulette_wheel_selection(pop_eval)

                p1_target_idx = p1_data[4] if p1_data[2] > 0 else p1_data[5]
                p2_target_idx = p2_data[4] if p2_data[2] > 0 else p2_data[5]

                o1, o2 = self._crossover(copy.deepcopy(p1_data[0]), copy.deepcopy(p2_data[0]), p1_target_idx,
                                         p2_target_idx)

                for child in [o1, o2]:
                    if len(new_pop) >= self.pop_size:
                        break

                    child_eval = self.evaluator.evaluate(child)
                    hc_c = child_eval[1]
                    active_idx_c = child_eval[3] if hc_c > 0 else child_eval[4]

                    m = self._mutation_M1(child, active_idx_c)
                    m_eval = self.evaluator.evaluate(m)
                    hc_m = m_eval[1]
                    active_idx_m = m_eval[3] if hc_m > 0 else m_eval[4]

                    d, d_eval = self._mutation_M2(m, active_idx_m, m_eval, gen)
                    active_idx_d = d_eval[3] if d_eval[1] > 0 else d_eval[4]

                    d, d_eval = self._mutation_M3(d, active_idx_d, d_eval, gen)

                    new_pop.append(d)

            current_pop = new_pop

        print("\n--- 优化结束 ---")
        if best_overall is not None:
            print(f"全局最优适应度: {best_overall[1]}")
            print(f"最终硬约束违规 (HC): {best_overall[2]}")
            print(f"最终软约束违规 (SC): {best_overall[3]}")
        return best_overall


def export_solution_to_csv(best_solution, parser, filename="timetable_result.csv"):
    """
    将最优排课方案导出为 CSV 文件
    """
    if not best_solution:
        print("没有可导出的有效解！")
        return

    # best_solution 是一个元组: (chromosome, fitness, hc_v, sc_v)
    chromosome = best_solution[0]

    # 获取每天的时段数，用于解码 timeslot
    periods_per_day = int(parser.metadata.get('Periods_per_day', 6))

    export_data = []

    for gene in chromosome:
        course_id = gene['course_id']
        subcourse_id = gene.get('subcourse_id', course_id)
        room_id = gene['room_id']
        timeslot = gene['timeslot']
        length = gene.get('length', 2)
        week_signal = gene.get('week_signal', 0)

        # 将一维的时间槽解码为"第几天"和"第几节课"
        day = timeslot // periods_per_day
        period = timeslot % periods_per_day

        # 周数标签
        week_label = {0: "每周", 1: "单周", 2: "双周"}.get(week_signal, "未知")

        export_data.append({
            'Day': day,
            'Period': period,
            'Room_ID': room_id,
            'Course_ID': course_id,
            'Subcourse_ID': subcourse_id,
            'Length': length,
            'Week_Signal': week_signal,
            'Week_Label': week_label,
            'Timeslot_Index': timeslot
        })

    # 为了方便阅读，按照时间 (Day -> Period -> Room) 进行排序
    export_data.sort(key=lambda x: (x['Day'], x['Period'], x['Room_ID']))

    # 写入 CSV 文件
    with open(filename, mode='w', newline='', encoding='utf-8') as f:
        fieldnames = ['Day', 'Period', 'Room_ID', 'Course_ID', 'Subcourse_ID', 'Length', 'Week_Signal', 'Week_Label', 'Timeslot_Index']
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        writer.writeheader()
        for row in export_data:
            writer.writerow(row)

    print(f"\n课表已成功导出至: {filename}")


# ================= 测试与执行入口 =================
if __name__ == "__main__":
    import argparse

    from CTTParser import CTTParser
    from PopulationInitializer import PopulationInitializer

    parser_args = argparse.ArgumentParser()
    parser_args.add_argument('--visualize', action='store_true', help='显示训练过程中的教室课表可视化')
    parser_args.add_argument('--visualize-every', type=int, default=5, help='每隔多少代刷新一次可视化')
    parser_args.add_argument('--dataset', default='comp21.ctt', help='训练使用的 CTT 数据文件')
    parser_args.add_argument('--pop-size', type=int, default=50, help='初始种群大小')
    parser_args.add_argument('--max-gen', type=int, default=30000, help='最大迭代代数')
    args = parser_args.parse_args()

    visualizer = None
    progress_callback = None

    if args.visualize:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from Visualization import TrainingProcessVisualizer

    # 1. 解析数据
    dataset_path = args.dataset
    if not os.path.isabs(dataset_path) and not os.path.exists(dataset_path):
        alt_dataset_path = os.path.join(os.path.dirname(__file__), dataset_path)
        if os.path.exists(alt_dataset_path):
            dataset_path = alt_dataset_path
    parser = CTTParser(dataset_path)

    if args.visualize:
        visualizer = TrainingProcessVisualizer(parser, update_every=args.visualize_every)
        progress_callback = visualizer

    # 2. 初始化种群
    initializer = PopulationInitializer(parser, pop_size=args.pop_size)
    initial_pop = initializer.initialize_population()

    # 3. 运行优化器
    optimizer = MDGAOptimizer(parser, initial_pop, max_gen=args.max_gen, progress_callback=progress_callback)

    try:
        best_solution = optimizer.run()
    finally:
        if visualizer:
            visualizer.close()

    # 4. 导出最终结果
    export_solution_to_csv(best_solution, parser, filename="best_timetable.csv")
